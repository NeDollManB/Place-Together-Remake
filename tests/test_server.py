#!/usr/bin/env python3
"""Regression tests for the PlaceTogether sync server.

Run with:  python -m unittest discover -s tests -v
No third-party dependencies: the suite starts a real HTTP server on a random
port against a temporary data directory.
"""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402


def http_json(base, path, payload=None, token=None, secret=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    if secret:
        headers["X-PT-Secret"] = secret
    req = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw.decode("utf-8", "replace")
            return e.code, parsed
        finally:
            e.close()


class ServerTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep the scratch directory inside the repo: some sandboxes only allow
        # writes under the workspace and restrict freshly made temp dirs.
        cls.tmpdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp", cls.__name__)
        os.makedirs(cls.tmpdir, exist_ok=True)
        cls._saved = {
            "data_filepath": server.data_filepath,
            "backups_dir": server.backups_dir,
            "auth_secret": server.auth_secret,
            "cors_origin": server.cors_origin,
            "rooms": server.rooms,
            "guests": server.guests,
            "invitations": server.invitations,
        }
        server.data_filepath = os.path.join(cls.tmpdir, "data.json")
        server.backups_dir = server.get_backups_dir(server.data_filepath)
        server.auth_secret = ""
        server.cors_origin = ""
        server.rooms = {}
        server.guests = {}
        server.invitations = []

        cls.httpd = server.ThreadedHTTPServer(("127.0.0.1", 0), server.PlaceTogetherHandler)
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.data_filepath = cls._saved["data_filepath"]
        server.backups_dir = cls._saved["backups_dir"]
        server.auth_secret = cls._saved["auth_secret"]
        server.cors_origin = cls._saved["cors_origin"]
        server.rooms = cls._saved["rooms"]
        server.guests = cls._saved["guests"]
        server.invitations = cls._saved["invitations"]
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def register(self, name="Tester", device_key=None):
        payload = {"name": name}
        if device_key:
            payload["deviceKey"] = device_key
        status, body = http_json(self.base, "/api/guest", payload)
        self.assertEqual(status, 200, body)
        return body["token"]

    def create_room(self, token, name="Test Place"):
        status, body = http_json(self.base, "/api/rooms", {"name": name}, token=token)
        self.assertEqual(status, 200, body)
        return body["id"]

    def sync(self, token, room_id, changes=None, since=0):
        payload = {"changes": changes or [], "since": since}
        return http_json(self.base, f"/api/rooms/{room_id}/sync", payload, token=token)

    def get_sync(self, token, room_id, since=0):
        return http_json(self.base, f"/api/rooms/{room_id}/sync?since={since}", token=token)


class TestAuth(ServerTestBase):
    def test_status_is_public(self):
        status, body = http_json(self.base, "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "online")
        self.assertFalse(body["auth_required"])

    def test_room_list_requires_registration(self):
        status, body = http_json(self.base, "/api/rooms")
        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_room_sync_requires_registration(self):
        self.register("AuthUser")
        room = self.create_room(self._token("AuthUser"))
        status, _ = http_json(self.base, f"/api/rooms/{room}/sync?since=0")
        self.assertEqual(status, 401)

    def test_secret_enforced_when_configured(self):
        server.auth_secret = "s3cret"
        try:
            status, _ = http_json(self.base, "/api/status")
            self.assertEqual(status, 401)
            status, body = http_json(self.base, "/api/status", secret="s3cret")
            self.assertEqual(status, 200)
            self.assertTrue(body["auth_required"])

            status, _ = http_json(self.base, "/api/guest", {"name": "NoSecret"})
            self.assertEqual(status, 401)
            status, body = http_json(self.base, "/api/guest", {"name": "WithSecret"}, secret="s3cret")
            self.assertEqual(status, 200)
            self.assertIn("token", body)
        finally:
            server.auth_secret = ""

    def _token(self, name):
        return self.register(name)

    def test_restore_is_owner_only(self):
        owner = self.register("Owner")
        guest = self.register("Guest")
        room = self.create_room(owner)
        self.sync(owner, room, [{"id": "k1", "value": {"class": "Part", "props": {"Name": "A"}}}], since=0)
        status, _ = http_json(self.base, f"/api/rooms/{room}/save", {"label": "t", "snapshot": True}, token=owner)
        self.assertEqual(status, 200)

        status, body = http_json(self.base, f"/api/rooms/{room}/restore", {}, token=guest)
        self.assertEqual(status, 403, body)

        status, body = http_json(self.base, f"/api/rooms/{room}/restore", {}, token=owner)
        self.assertEqual(status, 200, body)


class TestSyncProtocol(ServerTestBase):
    def setUp(self):
        self.token = self.register("Syncer")
        self.room = self.create_room(self.token)

    def test_full_then_delta_then_unchanged(self):
        value = {"class": "Part", "props": {"Name": "Part1", "Size": [1, 2, 3]}}
        status, body = self.sync(self.token, self.room, [{"id": "k1", "value": value}], since=0)
        self.assertEqual(status, 200, body)
        rev = body["revision"]
        self.assertEqual(rev, 2)

        status, body = self.get_sync(self.token, self.room, since=rev)
        self.assertTrue(body.get("unchanged"))

        status, body = self.get_sync(self.token, self.room, since=1)
        self.assertTrue(body.get("delta"))
        self.assertEqual(body["objects"]["k1"]["value"], value)

        status, body = self.get_sync(self.token, self.room, since=0)
        self.assertNotIn("delta", body)
        self.assertIn("k1", body["objects"])

    def test_delete_leaves_tombstone_and_late_joiner_does_not_resurrect(self):
        self.sync(self.token, self.room, [{"id": "k1", "value": {"class": "Part", "props": {"Name": "Gone"}}}], since=0)
        status, body = self.sync(self.token, self.room, [{"id": "k1", "deleted": True}], since=2)
        self.assertEqual(status, 200, body)
        delete_rev = body["revision"]

        status, body = self.get_sync(self.token, self.room, since=delete_rev - 1)
        self.assertEqual(body["objects"]["k1"]["value"], None)

        late = self.register("Late")
        status, body = self.get_sync(late, self.room, since=0)
        self.assertNotIn("k1", body["objects"])

    def test_noop_change_does_not_bump_revision(self):
        value = {"class": "Part", "props": {"Name": "Stable"}}
        status, body = self.sync(self.token, self.room, [{"id": "k1", "value": value}], since=0)
        rev = body["revision"]
        for _ in range(3):
            status, body = self.sync(self.token, self.room, [{"id": "k1", "value": value}], since=rev)
            self.assertEqual(body["revision"], rev, "identical writes must not create revisions")

    def test_sync_rejects_malformed_changes(self):
        status, body = http_json(
            self.base,
            f"/api/rooms/{self.room}/sync",
            {"changes": "not-a-list", "since": 0},
            token=self.token,
        )
        self.assertEqual(status, 400, body)


class TestPersistence(ServerTestBase):
    def test_save_writes_valid_json_and_no_temp_file(self):
        token = self.register("Saver")
        room = self.create_room(token)
        self.sync(token, room, [{"id": "k1", "value": {"class": "Part", "props": {"Name": "X"}}}], since=0)
        status, body = http_json(self.base, f"/api/rooms/{room}/save", {"label": "unit"}, token=token)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["saved"])

        self.assertTrue(os.path.exists(server.data_filepath))
        self.assertFalse(os.path.exists(server.data_filepath + ".tmp"))
        with open(server.data_filepath, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn(room, data["rooms"])
        self.assertIn("k1", data["rooms"][room]["objects"])
        self.assertGreaterEqual(len(server.list_room_backups(room)), 1)

    def test_concurrent_sync_does_not_break_saves(self):
        """Regression: serializing live room dicts outside the lock made saves fail."""
        token = self.register("Racer")
        room = self.create_room(token)
        errors = []
        stop = threading.Event()

        def writer(worker):
            i = 0
            while not stop.is_set() and i < 40:
                i += 1
                try:
                    self.sync(
                        token,
                        room,
                        [{"id": f"w{worker}_{i}", "value": {"class": "Part", "props": {"Name": f"p{i}"}}}],
                        since=0,
                    )
                except Exception as exc:  # pragma: no cover - only on failure
                    errors.append(exc)
                    return

        threads = [threading.Thread(target=writer, args=(n,), daemon=True) for n in range(4)]
        for t in threads:
            t.start()

        results = []
        for _ in range(15):
            results.append(server.save_data(server.data_filepath, force=True))

        stop.set()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertTrue(all(results), "every concurrent save must succeed")
        with open(server.data_filepath, encoding="utf-8") as f:
            json.load(f)


class TestRequestLimits(ServerTestBase):
    def raw(self, request_bytes):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            sock.sendall(request_bytes)
            chunks = []
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            sock.close()
        return b"".join(chunks)

    def test_invalid_content_length_is_400(self):
        raw = self.raw(
            b"POST /api/guest HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\nConnection: close\r\n\r\n"
        )
        self.assertIn(b"400", raw.split(b"\r\n")[0])

    def test_oversized_body_is_413(self):
        limit = server.MAX_BODY_BYTES
        server.MAX_BODY_BYTES = 256
        try:
            payload = b"x" * 512
            raw = self.raw(
                b"POST /api/guest HTTP/1.1\r\nHost: x\r\nContent-Length: 512\r\nConnection: close\r\n\r\n" + payload
            )
            self.assertIn(b"413", raw.split(b"\r\n")[0])
        finally:
            server.MAX_BODY_BYTES = limit


class TestTombstonePruning(unittest.TestCase):
    def test_pruning_never_drops_tombstones_a_peer_still_needs(self):
        room = {
            "revision": 40,
            "tombstones": {f"k{i}": {"version": i, "value": None} for i in range(1, 41)},
            "clients": {"a": {"since": 35, "time": server.time.time()}},
        }
        original = server.MAX_TOMBSTONES_PER_ROOM
        server.MAX_TOMBSTONES_PER_ROOM = 5
        try:
            server.prune_tombstones(room)
        finally:
            server.MAX_TOMBSTONES_PER_ROOM = original
        versions = sorted(t["version"] for t in room["tombstones"].values())
        self.assertEqual(versions, [36, 37, 38, 39, 40])

    def test_pruning_is_skipped_without_connected_peers(self):
        room = {
            "revision": 40,
            "tombstones": {f"k{i}": {"version": i, "value": None} for i in range(1, 41)},
            "clients": {},
        }
        original = server.MAX_TOMBSTONES_PER_ROOM
        server.MAX_TOMBSTONES_PER_ROOM = 5
        try:
            server.prune_tombstones(room)
        finally:
            server.MAX_TOMBSTONES_PER_ROOM = original
        self.assertEqual(len(room["tombstones"]), 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
