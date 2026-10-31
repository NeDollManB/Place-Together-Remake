#!/usr/bin/env python3
"""
PlaceTogether Ultra-Fast Co-op Server (v3.0)
High-performance synchronization & persistence backend for Roblox Studio co-op building.

Key Improvements in v3.0:
- Lossless Revision & Tombstone Engine: Delta sync (?since=rev) works across ANY revision gap
  and survives server restarts without losing deletions or edits.
- Live Drag Transform Streaming: Real-time 60 FPS peer object dragging via /presence.
- Smart Auto-Save + Multi-Slot Backup System:
  - Debounced background disk flush (within 2s of any edit) + periodic checkpointing
  - Atomic writes (.tmp -> os.replace) + .bak corruption recovery
  - Timestamped & named room snapshots in backups/ with 1-click live restore
  - Graceful shutdown hooks (atexit, SIGINT, SIGTERM, Windows SIGBREAK)
- Silent Fast Polling: Suppresses console I/O bottlenecks on high-frequency /sync and /presence requests.
"""

import sys
import os
import json
import time
import hmac
import shutil
import signal
import atexit
import hashlib
import difflib
import argparse
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

DEFAULT_PORT = 8787
DATA_FILE = "data.json"
MAX_BACKUPS_PER_ROOM = 25
MAX_TOMBSTONES_PER_ROOM = 5000
MAX_BODY_BYTES = 64 * 1024 * 1024   # hard cap for a single request body
GUEST_TTL_SECONDS = 30 * 24 * 3600 # forget guests that have not been seen for a month
INVITE_TTL_SECONDS = 24 * 3600     # forget finished invitations after a day
CLIENT_REV_TTL_SECONDS = 60        # how long a peer counts as "still connected"

# In-memory storage with re-entrant mutex
lock = threading.RLock()
rooms = {}       # room_id -> { name, owner, revision, objects, tombstones, presence, persistent, closed, members, last_saved, updated_at }
guests = {}      # token -> { name, deviceKey, last_seen }
invitations = [] # list of { id, from, nick, room, place, state, recipientOnline, delivered, created_at }
next_invite_id = 1

# Optional shared secret (server password). When set, every API call must send
# the X-PT-Secret header. Empty string means "no password" (legacy open mode).
auth_secret = ""
# Browser cross-origin access is disabled unless --cors-origin is passed.
cors_origin = ""

# Persistence state
data_filepath = DATA_FILE
backups_dir = "backups"
dirty_flag = False
last_change_time = 0.0
last_disk_save_time = 0.0
server_start_time = time.time()
save_lock = threading.Lock()


def get_backups_dir(base_data_file):
    base_dir = os.path.dirname(os.path.abspath(base_data_file))
    bdir = os.path.join(base_dir, "backups")
    os.makedirs(bdir, exist_ok=True)
    return bdir


def mark_dirty(room_id=None):
    global dirty_flag, last_change_time
    with lock:
        dirty_flag = True
        last_change_time = time.time()
        if room_id and room_id in rooms:
            rooms[room_id]["updated_at"] = last_change_time


def stable_hue(token):
    """Deterministic peer colour. builtin hash() is salted per process, so the
    same peer changed colour after every server restart."""
    digest = hashlib.sha1(str(token or "").encode("utf-8")).hexdigest()
    return (int(digest[:8], 16) % 100) / 100.0


def cleanup_stale():
    """Drop expired guests, invitations and presence entries.

    Guests and invitations used to grow without bound (memory + data.json).
    """
    global dirty_flag
    now = time.time()
    changed = False
    with lock:
        owners = {r.get("owner") for r in rooms.values()}
        stale_guests = [
            tok for tok, g in guests.items()
            if now - g.get("last_seen", 0) > GUEST_TTL_SECONDS and tok not in owners
        ]
        for tok in stale_guests:
            guests.pop(tok, None)
            changed = True

        kept = []
        for inv in invitations:
            age = now - inv.get("created_at", now)
            finished = inv.get("state") in ("accepted", "declined")
            if age > INVITE_TTL_SECONDS or (finished and age > 600):
                changed = True
                continue
            kept.append(inv)
        if len(kept) != len(invitations):
            invitations[:] = kept

        for r in rooms.values():
            presence = r.get("presence") or {}
            for p_id in [pid for pid, p in presence.items() if now - p.get("time", 0) > 120]:
                presence.pop(p_id, None)
            clients = r.get("clients") or {}
            for key in [k for k, v in clients.items() if now - v.get("time", 0) > CLIENT_REV_TTL_SECONDS]:
                clients.pop(key, None)

        if changed:
            dirty_flag = True
    return changed


def serialize_room(r):
    return {
        "name": r.get("name", "Studio Project"),
        "owner": r.get("owner", ""),
        "revision": r.get("revision", 1),
        "objects": r.get("objects", {}),
        "tombstones": r.get("tombstones", {}),
        "persistent": r.get("persistent", True),
        "closed": r.get("closed", False),
        "members": list(r.get("members", [])),
        "last_saved": r.get("last_saved", time.time()),
        "updated_at": r.get("updated_at", time.time()),
    }


def prune_tombstones(r):
    """Drop tombstones no connected peer can still need.

    Old behaviour kept an arbitrary half of the list, which silently resurrected
    deleted objects for peers that were further behind. Now a tombstone is only
    dropped once *every* recently active client already knows about it, i.e. its
    version is <= the smallest `since` revision any peer is polling with.
    """
    tombstones = r.get("tombstones") or {}
    if len(tombstones) <= MAX_TOMBSTONES_PER_ROOM:
        return

    now = time.time()
    since_values = [
        info.get("since", 0)
        for info in r.get("clients", {}).values()
        if now - info.get("time", 0) <= CLIENT_REV_TTL_SECONDS
    ]
    if not since_values:
        # Nobody is connected (or nobody reported a revision yet): pruning here
        # could resurrect deletions for a client that comes back, so keep them.
        return

    min_safe_rev = min(since_values)
    r["tombstones"] = {
        obj_id: tomb
        for obj_id, tomb in tombstones.items()
        if tomb.get("version", 0) > min_safe_rev
    }


def merge_3way_text(base_str, text_a, text_b):
    if text_a == text_b:
        return text_a
    if not text_a:
        return text_b
    if not text_b:
        return text_a

    lines_a = text_a.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines_b = text_b.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    res = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            res.extend(lines_a[i1:i2])
        elif tag == "replace":
            res.extend(lines_a[i1:i2])
            res.extend(lines_b[j1:j2])
        elif tag == "delete":
            res.extend(lines_a[i1:i2])
        elif tag == "insert":
            res.extend(lines_b[j1:j2])
    return "\n".join(res)


def save_data(filepath=None, force=False):
    global dirty_flag, last_disk_save_time
    if filepath is None:
        filepath = data_filepath

    # IMPORTANT: the whole snapshot is serialized while holding `lock`.
    # Serializing after releasing it used to race with /sync handlers mutating
    # room dictionaries mid-dump ("dictionary changed size during iteration"),
    # which made saves fail silently under load and lose edits.
    with lock:
        if not dirty_flag and not force:
            return False

        now = time.time()
        clean_rooms = {}
        for r_id, r in rooms.items():
            prune_tombstones(r)
            r["last_saved"] = now
            clean_rooms[r_id] = serialize_room(r)

        payload = {
            "version": "3.0.0",
            "saved_at": now,
            "rooms": clean_rooms,
            "guests": dict(guests),
        }
        dirty_flag = False
        last_disk_save_time = now

        try:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            dirty_flag = True
            print(f"[ERROR] Failed to serialize data: {e}", file=sys.stderr)
            return False

    with save_lock:
        try:
            abs_path = os.path.abspath(filepath)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            temp_path = abs_path + ".tmp"
            bak_path = abs_path + ".bak"

            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())

            if os.path.exists(abs_path):
                try:
                    shutil.copy2(abs_path, bak_path)
                except Exception:
                    pass

            os.replace(temp_path, abs_path)
            return True
        except Exception as e:
            print(f"[ERROR] Failed to save data to {filepath}: {e}", file=sys.stderr)
            with lock:
                dirty_flag = True
            return False


def create_room_backup(room_id, label="auto"):
    with lock:
        if room_id not in rooms:
            return None
        r = rooms[room_id]
        room_data = serialize_room(r)
        rev = r["revision"]
        obj_count = len(r.get("objects", {}))

    try:
        bdir = get_backups_dir(data_filepath)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(c for c in label if c.isalnum() or c in ("-", "_"))[:24] or "snap"
        filename = f"room_{room_id}_{ts}_rev{rev}_{safe_label}.json"
        full_path = os.path.join(bdir, filename)

        backup_payload = {
            "backup_id": filename,
            "room_id": room_id,
            "label": label,
            "timestamp": time.time(),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "revision": rev,
            "objects_count": obj_count,
            "room": room_data,
        }

        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(backup_payload, f, ensure_ascii=False, separators=(",", ":"))

        # Rotate old backups for this room
        prefix = f"room_{room_id}_"
        existing = sorted(
            [fn for fn in os.listdir(bdir) if fn.startswith(prefix) and fn.endswith(".json")]
        )
        while len(existing) > MAX_BACKUPS_PER_ROOM:
            oldest = existing.pop(0)
            try:
                os.remove(os.path.join(bdir, oldest))
            except Exception:
                pass

        return backup_payload
    except Exception as e:
        print(f"[WARN] Failed to create room backup for {room_id}: {e}", file=sys.stderr)
        return None


def list_room_backups(room_id):
    bdir = get_backups_dir(data_filepath)
    prefix = f"room_{room_id}_"
    results = []
    try:
        files = sorted(
            [fn for fn in os.listdir(bdir) if fn.startswith(prefix) and fn.endswith(".json")],
            reverse=True,
        )
        for fn in files:
            fpath = os.path.join(bdir, fn)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                results.append({
                    "id": fn,
                    "label": meta.get("label", "backup"),
                    "date": meta.get("date", fn),
                    "timestamp": meta.get("timestamp", 0),
                    "revision": meta.get("revision", 1),
                    "objectsCount": meta.get("objects_count", len(meta.get("room", {}).get("objects", {}))),
                })
            except Exception:
                continue
    except Exception:
        pass
    return results


def restore_room_backup(room_id, backup_id=None):
    bdir = get_backups_dir(data_filepath)
    if not backup_id:
        backups = list_room_backups(room_id)
        if not backups:
            return None, "Нет доступных резервных копий для этой комнаты"
        backup_id = backups[0]["id"]

    # Security check against path traversal
    backup_id = os.path.basename(backup_id)
    fpath = os.path.join(bdir, backup_id)
    if not os.path.exists(fpath):
        return None, "Файл резервной копии не найден"

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            backup_payload = json.load(f)
        saved_room = backup_payload.get("room", {})
        saved_objects = saved_room.get("objects", {})
    except Exception as e:
        return None, f"Ошибка чтения бэкапа: {e}"

    # Create a safety snapshot before restoring
    create_room_backup(room_id, label="pre_restore")

    with lock:
        if room_id not in rooms:
            return None, "Комната не найдена"
        r = rooms[room_id]
        new_rev = r["revision"] + 1
        r["revision"] = new_rev

        # Mark objects that exist currently but not in backup as tombstones at new_rev
        current_keys = set(r["objects"].keys())
        restored_keys = set(saved_objects.keys())
        removed_keys = current_keys - restored_keys

        tombstones = r.setdefault("tombstones", {})
        for rem_key in removed_keys:
            tombstones[rem_key] = {"version": new_rev, "value": None}

        # Set all restored objects to new_rev so connected clients immediately pull them via delta sync
        new_objects = {}
        for obj_id, rec in saved_objects.items():
            val = rec.get("value") if isinstance(rec, dict) and "value" in rec else rec
            new_objects[obj_id] = {"version": new_rev, "value": val}
            if obj_id in tombstones:
                del tombstones[obj_id]

        r["objects"] = new_objects
        mark_dirty(room_id)

    save_data(data_filepath, force=True)
    print(f"[RESTORE] Room {room_id} restored from {backup_id} -> rev {new_rev} ({len(new_objects)} objects)")
    return {
        "restored": True,
        "backupId": backup_id,
        "revision": new_rev,
        "objectsCount": len(new_objects),
    }, None


def load_data(filepath):
    global rooms, guests, last_disk_save_time
    abs_path = os.path.abspath(filepath)
    bak_path = abs_path + ".bak"

    target_to_load = None
    if os.path.exists(abs_path):
        target_to_load = abs_path
    elif os.path.exists(bak_path):
        print(f"[WARN] Main data file missing, recovering from {bak_path}")
        target_to_load = bak_path

    if not target_to_load:
        return

    with lock:
        data = None
        try:
            with open(target_to_load, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WARN] Error loading {target_to_load}: {e}")
            if target_to_load == abs_path and os.path.exists(bak_path):
                try:
                    print(f"[INFO] Attempting recovery from backup {bak_path}...")
                    with open(bak_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as e2:
                    print(f"[ERROR] Backup recovery also failed: {e2}")

        if not data:
            return

        now = time.time()
        for r_id, r in data.get("rooms", {}).items():
            rooms[r_id] = {
                "name": r.get("name", "Room"),
                "owner": r.get("owner", ""),
                "revision": int(r.get("revision", 1)),
                "objects": r.get("objects", {}),
                "tombstones": r.get("tombstones", {}),
                "presence": {},
                "persistent": r.get("persistent", True),
                "closed": r.get("closed", False),
                "members": set(r.get("members", [])),
                "last_saved": r.get("last_saved", now),
                "updated_at": r.get("updated_at", now),
            }
        guests.update(data.get("guests", {}))
        last_disk_save_time = data.get("saved_at", now)
        total_objs = sum(len(r["objects"]) for r in rooms.values())
        print(f"[INFO] Loaded {len(rooms)} rooms ({total_objs} objects) and {len(guests)} guests from {target_to_load}")


class BodyTooLarge(Exception):
    pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


class PlaceTogetherHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # Without a timeout a half-open keep-alive connection (VPN drop, ngrok
    # restart) keeps a worker thread alive forever and threads pile up.
    timeout = 30

    def handle(self):
        # A peer that drops its tunnel (VPN reconnect, Studio restart) resets the
        # keep-alive socket; socketserver would dump a full traceback for that.
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, TimeoutError):
            pass

    def log_message(self, format, *args):
        # Suppress console spam on high-frequency /sync, /presence, and /invitations polling
        # Console printing on Windows cmd.exe blocks threads and causes lag spikes.
        path = getattr(self, "path", "")
        if any(p in path for p in ("/sync", "/presence", "/invitations", "/status", "/state", "/rooms")):
            return
        sys.stderr.write("%s - - [%s] %s\n" % (
            self.address_string(),
            self.log_date_time_string(),
            format % args
        ))

    def send_json(self, status_code, data):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # Cross-origin access is opt-in: a wildcard CORS policy let any web
            # page in the user's browser drive this API (CSRF on 127.0.0.1).
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-PT-Secret, bypass-tunnel-reminder")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            # If the request body was not fully consumed the connection cannot be
            # reused: answering keep-alive there desynchronises the next request.
            self.send_header("Connection", "close" if self.close_connection else "keep-alive")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-PT-Secret, bypass-tunnel-reminder")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def get_token(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        return None

    def secret_ok(self):
        """Optional server password (--secret / PT_SECRET)."""
        if not auth_secret:
            return True
        supplied = self.headers.get("X-PT-Secret", "")
        return hmac.compare_digest(str(supplied), auth_secret)

    def require_secret(self):
        if self.secret_ok():
            return True
        self.send_json(401, {"error": "Неверный пароль сервера (X-PT-Secret)"})
        return False

    def require_guest(self, token):
        """Every room endpoint needs a registered guest token."""
        if token and token in guests:
            return True
        self.send_json(401, {"error": "Нужна регистрация гостя. Переподключитесь к серверу."})
        return False

    def read_body(self):
        raw_length = self.headers.get("Content-Length")
        try:
            content_length = int(raw_length or 0)
        except (TypeError, ValueError):
            raise ValueError("invalid Content-Length header")
        if content_length < 0:
            raise ValueError("negative Content-Length header")
        if content_length > MAX_BODY_BYTES:
            raise BodyTooLarge(f"body larger than {MAX_BODY_BYTES} bytes")
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            # Roblox HttpService:JSONEncode({}) serializes empty Lua tables as "[]".
            # Treat an empty list as an empty object so Roblox client requests never fail.
            if len(parsed) == 0:
                return {}
            raise ValueError("JSON body must be an object")
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    def remember_client_revision(self, r, token, since_rev):
        """Track how far behind each connected peer is (drives tombstone pruning)."""
        clients = r.setdefault("clients", {})
        clients[token or "anonymous"] = {
            "since": max(0, int(since_rev or 0)),
            "time": time.time(),
        }
        if len(clients) > 64:
            now = time.time()
            stale = [k for k, v in clients.items() if now - v.get("time", 0) > CLIENT_REV_TTL_SECONDS]
            for key in stale:
                clients.pop(key, None)

    def compute_deltas_since(self, r, since_rev):
        deltas = {}
        for obj_id, rec in r["objects"].items():
            if rec.get("version", 0) > since_rev:
                deltas[obj_id] = rec
        for obj_id, tomb in r.get("tombstones", {}).items():
            if tomb.get("version", 0) > since_rev:
                deltas[obj_id] = {"version": tomb["version"], "value": None}
        return deltas

    def do_GET(self):
        path = self.path.split("?")[0]
        query_string = self.path.split("?")[1] if "?" in self.path else ""
        query_params = {}
        if query_string:
            for pair in query_string.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    query_params[k] = v

        token = self.get_token()

        # 1. Health / Status check (public on purpose, contains no user data)
        if path == "/api/status" or path == "/":
            if not self.secret_ok():
                self.send_json(401, {"error": "Неверный пароль сервера"})
                return
            with lock:
                self.send_json(200, {
                    "server": "PlaceTogether-UltraFast",
                    "version": "3.0.0",
                    "status": "online",
                    "uptime": int(time.time() - server_start_time),
                    "rooms_count": len(rooms),
                    "active_guests": len(guests),
                    "last_saved": last_disk_save_time,
                    "auth_required": bool(auth_secret),
                })
            return

        if not self.require_secret():
            return

        # 2. Get Rooms list
        if path == "/api/rooms":
            if not self.require_guest(token):
                return
            with lock:
                now = time.time()
                res = []
                for r_id, r in rooms.items():
                    active_peers = [
                        p.get("name", "Peer")
                        for p in r.get("presence", {}).values()
                        if now - p.get("time", 0) < 15
                    ]
                    active_testers = [
                        p.get("name", "Peer")
                        for p in r.get("presence", {}).values()
                        if p.get("teamTest", {}).get("active") and now - p.get("time", 0) < 15
                    ]
                    res.append({
                        "id": r_id,
                        "name": r["name"],
                        "closed": r.get("closed", False),
                        "persistent": r.get("persistent", True),
                        "members": list(r.get("members", [])),
                        "activePeers": active_peers,
                        "activeTesters": active_testers,
                        "teamTestActive": len(active_testers) > 0,
                        "revision": r.get("revision", 1),
                        "objectsCount": len(r.get("objects", {})),
                        "lastSaved": r.get("last_saved", 0),
                        "updatedAt": r.get("updated_at", 0),
                    })
                res.sort(key=lambda x: x.get("updatedAt", 0), reverse=True)
                self.send_json(200, res)
            return

        # 3. Room sync GET: /api/rooms/<id>/sync with support for ?since=<rev>
        if path.startswith("/api/rooms/") and path.endswith("/sync"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            room_id = parts[3]
            with lock:
                if room_id not in rooms:
                    self.send_json(404, {"error": "Комната не найдена"})
                    return
                r = rooms[room_id]
                try:
                    since_rev = int(query_params.get("since", 0))
                except ValueError:
                    since_rev = 0
                self.remember_client_revision(r, token, since_rev)

                # Client is already up to date
                if since_rev > 0 and since_rev == r["revision"]:
                    self.send_json(200, {
                        "revision": r["revision"],
                        "unchanged": True,
                        "objects": {},
                        "lastSaved": r.get("last_saved", 0),
                        "objectsCount": len(r["objects"]),
                    })
                    return

                # Incremental delta since revision (lossless via object versions + tombstones)
                if 0 < since_rev < r["revision"]:
                    deltas = self.compute_deltas_since(r, since_rev)
                    self.send_json(200, {
                        "revision": r["revision"],
                        "unchanged": len(deltas) == 0,
                        "objects": deltas,
                        "delta": True,
                        "lastSaved": r.get("last_saved", 0),
                        "objectsCount": len(r["objects"]),
                    })
                    return

                # Full sync (initial join or reset)
                self.send_json(200, {
                    "revision": r["revision"],
                    "objects": r["objects"],
                    "owner": r["owner"],
                    "lastSaved": r.get("last_saved", 0),
                    "objectsCount": len(r["objects"]),
                })
            return

        # 4. Room state: /api/rooms/<id>/state
        if path.startswith("/api/rooms/") and path.endswith("/state"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            room_id = parts[3]
            with lock:
                if room_id not in rooms:
                    self.send_json(404, {"error": "Комната не найдена"})
                    return
                r = rooms[room_id]
                now = time.time()
                active_testers = [
                    p.get("name", "Peer")
                    for p in r.get("presence", {}).values()
                    if p.get("teamTest", {}).get("active") and now - p.get("time", 0) < 15
                ]
                self.send_json(200, {
                    "id": room_id,
                    "name": r["name"],
                    "isOwner": (r["owner"] == token),
                    "closed": r.get("closed", False),
                    "persistent": r.get("persistent", True),
                    "revision": r.get("revision", 1),
                    "objectsCount": len(r.get("objects", {})),
                    "lastSaved": r.get("last_saved", 0),
                    "activeTesters": active_testers,
                    "teamTestActive": len(active_testers) > 0,
                })
            return

        # 5. Room backups list: /api/rooms/<id>/backups
        if path.startswith("/api/rooms/") and path.endswith("/backups"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            room_id = parts[3]
            with lock:
                if room_id not in rooms:
                    self.send_json(404, {"error": "Комната не найдена"})
                    return
            backups = list_room_backups(room_id)
            self.send_json(200, backups)
            return

        # 6. Invitations inbox
        if path == "/api/invitations":
            if not self.require_guest(token):
                return
            with lock:
                if token in guests:
                    guests[token]["last_seen"] = time.time()
                user_name = guests.get(token, {}).get("name", "")
                pending = []
                for inv in invitations:
                    if inv.get("nick", "").lower() == user_name.lower() and inv.get("state") == "pending":
                        inv["delivered"] = True
                        pending.append(inv)
                self.send_json(200, pending)
            return

        # 7. Invitations outbox
        if path == "/api/invitations/outbox":
            if not self.require_guest(token):
                return
            with lock:
                user_name = guests.get(token, {}).get("name", "")
                now = time.time()
                my_invites = []
                for inv in invitations:
                    if inv.get("from") == user_name:
                        target_nick = inv.get("nick", "").lower()
                        inv["recipientOnline"] = any(
                            g.get("name", "").lower() == target_nick and (now - g.get("last_seen", 0) < 30)
                            for g in guests.values()
                        )
                        my_invites.append(inv)
                self.send_json(200, my_invites)
            return

        self.send_json(404, {"error": "Endpoint not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            data = self.read_body()
        except BodyTooLarge:
            self.close_connection = True
            self.send_json(413, {"error": "Слишком большое тело запроса"})
            return
        except Exception as e:
            self.close_connection = True
            self.send_json(400, {"error": f"Invalid JSON body: {e}"})
            return
        token = self.get_token()

        if not self.require_secret():
            return

        # 1. Guest Registration
        if path == "/api/guest":
            name = str(data.get("name", "Guest")).strip() or "Guest"
            device_key = str(data.get("deviceKey", "")).strip()
            if device_key and (len(device_key) > 128 or not device_key.isprintable()):
                self.send_json(400, {"error": "Invalid deviceKey"})
                return
            with lock:
                guest_token = device_key if device_key else (token or f"token_{int(time.time()*1000)}")
                old_name = guests.get(guest_token, {}).get("name")
                guests[guest_token] = {
                    "name": name,
                    "deviceKey": device_key,
                    "last_seen": time.time(),
                }
                if old_name and old_name != name:
                    for r in rooms.values():
                        if old_name in r.get("members", set()):
                            r["members"].discard(old_name)
                            r["members"].add(name)
                mark_dirty()
                print(f"[AUTH] User connected: {name} ({guest_token[:8]}...)")
                self.send_json(200, {
                    "token": guest_token,
                    "name": name,
                })
            return

        # 2. Create Room
        if path == "/api/rooms":
            if not self.require_guest(token):
                return
            name = str(data.get("name", "Studio Project")).strip() or "Studio Project"
            with lock:
                user_name = guests.get(token, {}).get("name", "User")
                room_id = f"{int(time.time()):x}{len(rooms)+1:04x}"
                now = time.time()
                rooms[room_id] = {
                    "name": name,
                    "owner": token,
                    "revision": 1,
                    "objects": {},
                    "tombstones": {},
                    "presence": {},
                    "persistent": True,
                    "closed": False,
                    "members": {user_name},
                    "last_saved": now,
                    "updated_at": now,
                }
                mark_dirty(room_id)
                print(f"[ROOM] Created room '{name}' (id={room_id}) by {user_name}")
                self.send_json(200, {
                    "id": room_id,
                    "name": name,
                })
            save_data(data_filepath, force=True)
            return

        # 3. Join / Resume Existing Room: /api/rooms/<id>/join
        if path.startswith("/api/rooms/") and path.endswith("/join"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            room_id = parts[3]
            with lock:
                if room_id not in rooms:
                    self.send_json(404, {"error": "Комната не найдена"})
                    return
                r = rooms[room_id]
                user_name = guests.get(token, {}).get("name", "User")
                r.setdefault("members", set()).add(user_name)
                mark_dirty(room_id)
                print(f"[ROOM] {user_name} joined room '{r['name']}' (id={room_id})")
                self.send_json(200, {
                    "id": room_id,
                    "name": r["name"],
                    "revision": r["revision"],
                    "objectsCount": len(r["objects"]),
                })
            return

        # 4. Synchronize POST: /api/rooms/<id>/sync
        if path.startswith("/api/rooms/") and path.endswith("/sync"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            room_id = parts[3]
            with lock:
                if room_id not in rooms:
                    self.send_json(404, {"error": "Комната не найдена"})
                    return
                r = rooms[room_id]
                changes = data.get("changes", [])
                if not isinstance(changes, list):
                    self.send_json(400, {"error": "changes must be a list"})
                    return
                if len(changes) > 20000:
                    self.send_json(413, {"error": "Слишком много изменений за один запрос"})
                    return
                try:
                    since_rev = int(data.get("since", 0))
                except (TypeError, ValueError):
                    since_rev = 0
                self.remember_client_revision(r, token, since_rev)

                # Collect remote changes that occurred since client's known revision BEFORE applying client's batch
                remote_deltas = {}
                if since_rev > 0 and since_rev < r["revision"]:
                    remote_deltas = self.compute_deltas_since(r, since_rev)

                applied_count = 0
                new_rev = r["revision"] + 1
                tombstones = r.setdefault("tombstones", {})

                for ch in changes:
                    obj_id = ch.get("id")
                    if not obj_id:
                        continue
                    if ch.get("deleted"):
                        if obj_id in r["objects"] or obj_id not in tombstones:
                            r["objects"].pop(obj_id, None)
                            tombstones[obj_id] = {"version": new_rev, "value": None}
                            applied_count += 1
                    else:
                        val = ch.get("value")
                        if obj_id in r["objects"] and isinstance(val, dict):
                            old_rec = r["objects"][obj_id]
                            old_val = old_rec.get("value") if isinstance(old_rec, dict) else None
                            if isinstance(old_val, dict) and old_rec.get("version", 0) > since_rev:
                                cur_props = old_val.get("props")
                                new_props = val.get("props")
                                if isinstance(cur_props, dict) and isinstance(new_props, dict):
                                    if "Source" in cur_props and "Source" in new_props:
                                        srv_src = cur_props["Source"]
                                        cli_src = new_props["Source"]
                                        if srv_src and cli_src and srv_src != cli_src:
                                            val["props"]["Source"] = merge_3way_text("", srv_src, cli_src)
                        # Skip writes that would not change anything: idle polls used
                        # to bump the room revision and make every peer re-download
                        # a delta that carries no information.
                        existing = r["objects"].get(obj_id)
                        if (
                            isinstance(existing, dict)
                            and obj_id not in tombstones
                            and existing.get("value") == val
                        ):
                            continue
                        rec = {"version": new_rev, "value": val}
                        r["objects"][obj_id] = rec
                        tombstones.pop(obj_id, None)
                        applied_count += 1

                if applied_count > 0:
                    r["revision"] = new_rev
                    mark_dirty(room_id)

                self.send_json(200, {
                    "revision": r["revision"],
                    "applied": True,
                    "remoteChanges": remote_deltas,
                    "lastSaved": r.get("last_saved", 0),
                    "objectsCount": len(r["objects"]),
                })
            return

        # 5. Presence / Camera / Live Drag POST: /api/rooms/<id>/presence
        if path.startswith("/api/rooms/") and path.endswith("/presence"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            room_id = parts[3]
            with lock:
                if room_id not in rooms:
                    self.send_json(404, {"error": "Room not found"})
                    return
                r = rooms[room_id]
                if token in guests:
                    guests[token]["last_seen"] = time.time()
                user_info = guests.get(token, {})
                user_name = user_info.get("name", "Peer")

                if data.get("leave"):
                    r["presence"].pop(token, None)
                else:
                    r["presence"][token] = {
                        "id": token,
                        "name": user_name,
                        "position": data.get("position", [0, 10, 0]),
                        "look": data.get("look", [0, 0, -1]),
                        "selected": data.get("selected", []),
                        "transforms": data.get("transforms", {}),
                        "teamTest": data.get("teamTest", None),
                        "activeScript": data.get("activeScript", None),
                        "hue": stable_hue(token),
                        "time": time.time(),
                    }

                # Return list of active other peers (last 12s)
                now = time.time()
                active_peers = []
                for p_id, p in list(r["presence"].items()):
                    if now - p.get("time", 0) < 12:
                        if p_id != token:
                            active_peers.append(p)
                    else:
                        del r["presence"][p_id]

                self.send_json(200, active_peers)
            return

        # 6. Invite Friend POST: /api/invitations
        if path == "/api/invitations":
            if not self.require_guest(token):
                return
            global next_invite_id
            target_nick = str(data.get("nick", "")).strip()
            room_id = str(data.get("room", "")).strip()
            with lock:
                inv_id = str(next_invite_id)
                next_invite_id += 1
                sender_name = guests.get(token, {}).get("name", "Host")
                now = time.time()
                is_online = any(
                    g.get("name", "").lower() == target_nick.lower() and (now - g.get("last_seen", 0) < 30)
                    for g in guests.values()
                )
                inv = {
                    "id": inv_id,
                    "from": sender_name,
                    "nick": target_nick,
                    "room": room_id,
                    "place": rooms.get(room_id, {}).get("name", "Studio Project"),
                    "state": "pending",
                    "recipientOnline": is_online,
                    "delivered": False,
                    "created_at": now,
                }
                invitations.append(inv)
                print(f"[INVITE] {sender_name} invited {target_nick} to room {room_id}")
                self.send_json(200, inv)
            return

        # 7. Respond to Invitation POST: /api/invitations/<id>/respond
        if path.startswith("/api/invitations/") and path.endswith("/respond"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            inv_id = parts[3]
            decision = data.get("decision", "accept")
            with lock:
                for inv in invitations:
                    if inv.get("id") == inv_id:
                        inv["state"] = "accepted" if decision == "accept" else "declined"
                        room_id = inv.get("room")
                        r = rooms.get(room_id, {})
                        user_name = guests.get(token, {}).get("name", "User")
                        if decision == "accept" and r:
                            r.setdefault("members", set()).add(user_name)
                            mark_dirty(room_id)
                        print(f"[INVITE] {user_name} {inv['state']} invite #{inv_id} for room {room_id}")
                        self.send_json(200, {
                            "decision": decision,
                            "room": {"id": room_id, "name": r.get("name", "Studio Project")},
                        })
                        return
                self.send_json(404, {"error": "Приглашение не найдено"})
            return

        # 8. Save Room & Create Backup: /api/rooms/<id>/save
        if path.startswith("/api/rooms/") and path.endswith("/save"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            room_id = parts[3]
            label = str(data.get("label", "manual")).strip() or "manual"
            create_snap = data.get("snapshot", True)
            with lock:
                if room_id not in rooms:
                    self.send_json(404, {"error": "Room not found"})
                    return
                r = rooms[room_id]
                if data.get("name"):
                    r["name"] = str(data["name"]).strip() or r["name"]
                r["persistent"] = True
                mark_dirty(room_id)
                rev = r["revision"]
                obj_count = len(r["objects"])

            saved_ok = save_data(data_filepath, force=True)
            backup_info = create_room_backup(room_id, label=label) if create_snap else None
            print(f"[SAVE] Room {room_id} saved (rev={rev}, objects={obj_count}, snapshot={bool(backup_info)})")
            self.send_json(200, {
                "saved": saved_ok,
                "persistent": True,
                "revision": rev,
                "objectsCount": obj_count,
                "lastSaved": time.time(),
                "backup": {
                    "id": backup_info["backup_id"],
                    "date": backup_info["date"],
                } if backup_info else None,
            })
            return

        # 9. Restore Room from Backup: /api/rooms/<id>/restore
        # Restoring rewrites every object of the room, so only the owner may do it.
        if path.startswith("/api/rooms/") and path.endswith("/restore"):
            if not self.require_guest(token):
                return
            parts = path.split("/")
            room_id = parts[3]
            backup_id = data.get("backupId")
            with lock:
                r = rooms.get(room_id)
                if r is None:
                    self.send_json(404, {"error": "Комната не найдена"})
                    return
                is_owner = r.get("owner") == token
            if not is_owner:
                self.send_json(403, {"error": "Восстановить комнату может только её владелец"})
                return
            result, err = restore_room_backup(room_id, backup_id)
            if err:
                self.send_json(400, {"error": err})
            else:
                self.send_json(200, result)
            return

        self.send_json(404, {"error": "Endpoint not found"})


def setup_shutdown_hooks():
    def on_exit(*args):
        if dirty_flag:
            print("\n[INFO] Flushing unsaved changes to disk before exit...")
            save_data(data_filepath, force=True)

    atexit.register(on_exit)
    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, lambda s, f: sys.exit(0))
            except Exception:
                pass


def main():
    global data_filepath, backups_dir, auth_secret, cors_origin
    parser = argparse.ArgumentParser(description="PlaceTogether Ultra-Fast Server v3.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to bind (default: {DEFAULT_PORT})")
    parser.add_argument("--data", type=str, default=DATA_FILE, help="Path to data JSON file")
    parser.add_argument(
        "--secret",
        type=str,
        default=os.environ.get("PT_SECRET", ""),
        help="Shared password. When set, clients must send X-PT-Secret (env: PT_SECRET)",
    )
    parser.add_argument(
        "--cors-origin",
        type=str,
        default=os.environ.get("PT_CORS_ORIGIN", ""),
        help="Allow browser cross-origin access from this exact origin (default: disabled)",
    )
    args = parser.parse_args()

    auth_secret = str(args.secret or "")
    cors_origin = str(args.cors_origin or "")
    data_filepath = os.path.abspath(args.data)
    backups_dir = get_backups_dir(data_filepath)

    load_data(data_filepath)
    setup_shutdown_hooks()

    # Smart background auto-saver:
    # - Saves within 2 seconds after edits settle (debounced) or at least every 15 seconds if continuously editing
    # - Also creates periodic automatic room backups every 10 minutes when changes occur
    def auto_saver():
        last_auto_backup_rev = {r_id: r["revision"] for r_id, r in rooms.items()}
        last_backup_check = time.time()
        last_cleanup = time.time()
        while True:
            time.sleep(1.0)
            now = time.time()
            should_save = False
            with lock:
                if dirty_flag:
                    # Save if 2s elapsed since last change, or 12s elapsed since last disk write
                    if (now - last_change_time >= 2.0) or (now - last_disk_save_time >= 12.0):
                        should_save = True
            if should_save:
                save_data(data_filepath)

            if now - last_cleanup >= 60.0:
                last_cleanup = now
                cleanup_stale()

            # Periodic auto-snapshot every 5 minutes for rooms with new revisions
            if now - last_backup_check >= 300.0:
                last_backup_check = now
                with lock:
                    room_ids = list(rooms.keys())
                for r_id in room_ids:
                    with lock:
                        r = rooms.get(r_id)
                        if not r:
                            continue
                        cur_rev = r["revision"]
                        obj_cnt = len(r.get("objects", {}))
                    if obj_cnt > 0 and last_auto_backup_rev.get(r_id, 0) != cur_rev:
                        create_room_backup(r_id, label="autosave")
                        last_auto_backup_rev[r_id] = cur_rev

    threading.Thread(target=auto_saver, daemon=True).start()

    server_address = ("0.0.0.0", args.port)
    httpd = ThreadedHTTPServer(server_address, PlaceTogetherHandler)
    print("============================================================")
    print(f"  PlaceTogether Ultra-Fast Server 3.0 running on port {args.port}")
    print(f"  Data File : {data_filepath}")
    print(f"  Backups   : {backups_dir}")
    print(f"  Password  : {'enabled (X-PT-Secret)' if auth_secret else 'disabled (open on the network!)'}")
    print("  Features  : Lossless Delta Sync | Live 60FPS Drag | Auto-Save")
    print("============================================================")
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\n[INFO] Shutting down server and saving data...")
        save_data(data_filepath, force=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
