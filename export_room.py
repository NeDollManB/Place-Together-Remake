#!/usr/bin/env python3
"""PlaceTogether Room Exporter & Inspector.

Inspects PlaceTogether server persistence files (data.json or backups/)
and exports rooms to standalone JSON snapshots or place summaries without
needing Roblox Studio open.

Usage:
    python export_room.py --list
    python export_room.py --room <room_id> [--data data.json] [--out export.json]
"""

import os
import sys
import json
import argparse
from datetime import datetime


def load_database(path):
    if not os.path.exists(path):
        print(f"[ERROR] Database file not found: {path}", file=sys.stderr)
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to parse {path}: {e}", file=sys.stderr)
        return None


def list_rooms(db):
    rooms = db.get("rooms", {})
    if not rooms:
        print("No rooms found in database.")
        return

    print("=" * 72)
    print(f"{'Room ID':<16} {'Revision':<10} {'Objects':<10} {'Room Name':<24} {'Owner'}")
    print("=" * 72)
    for r_id, r in rooms.items():
        name = r.get("name", "Studio Project")
        rev = r.get("revision", 1)
        obj_count = len(r.get("objects", {}))
        owner = r.get("owner_name", "Unknown")
        print(f"{r_id:<16} {rev:<10} {obj_count:<10} {name[:23]:<24} {owner}")
    print("=" * 72)
    print(f"Total Rooms: {len(rooms)}")


def inspect_and_export_room(db, room_id, out_file=None):
    rooms = db.get("rooms", {})
    if room_id not in rooms:
        print(f"[ERROR] Room ID '{room_id}' not found in database.", file=sys.stderr)
        return False

    r = rooms[room_id]
    objects = r.get("objects", {})
    classes = {}
    total_scripts = 0
    total_parts = 0
    total_models = 0
    total_terrain_chunks = 0

    for key, rec in objects.items():
        val = rec.get("value") if isinstance(rec, dict) and "value" in rec else rec
        if not isinstance(val, dict):
            continue
        c = val.get("class", "Unknown")
        classes[c] = classes.get(c, 0) + 1
        if "Script" in c:
            total_scripts += 1
        elif "Part" in c:
            total_parts += 1
        elif c == "Model":
            total_models += 1
        elif c == "PTTerrainChunk":
            total_terrain_chunks += 1

    print("\n" + "=" * 50)
    print(f" Room Details: {r.get('name', 'Studio Project')} ({room_id})")
    print("=" * 50)
    print(f" Owner       : {r.get('owner_name', 'Unknown')}")
    print(f" Revision    : {r.get('revision', 1)}")
    print(f" Total Objs  : {len(objects):,}")
    print(f" Parts       : {total_parts:,}")
    print(f" Models      : {total_models:,}")
    print(f" Scripts     : {total_scripts:,}")
    print(f" Terrain     : {total_terrain_chunks:,} chunks")
    print(f" Tombstones  : {len(r.get('tombstones', {})):,}")
    print(f" Chat History: {len(r.get('chat', [])):,} messages")

    top_classes = sorted(classes.items(), key=lambda x: x[1], reverse=True)[:8]
    if top_classes:
        print("\n Top Class Breakdown:")
        for c, count in top_classes:
            print(f"   - {c:<20}: {count:,}")
    print("=" * 50)

    if out_file:
        try:
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(r, f, indent=2, ensure_ascii=False)
            print(f"\n[OK] Successfully exported room to: {out_file} ({os.path.getsize(out_file):,} bytes)")
        except Exception as e:
            print(f"[ERROR] Failed to write export file: {e}", file=sys.stderr)
            return False

    return True


def main():
    parser = argparse.ArgumentParser(description="PlaceTogether Room Exporter & Inspector")
    parser.add_argument("--data", default="data.json", help="Path to data.json (default: data.json)")
    parser.add_argument("--list", action="store_true", help="List all rooms in database")
    parser.add_argument("--room", help="Room ID to inspect or export")
    parser.add_argument("--out", help="Output file path for room JSON export")
    args = parser.parse_args()

    db = load_database(args.data)
    if not db:
        return 1

    if args.list or not args.room:
        list_rooms(db)
        if not args.room:
            print("\nUse --room <room_id> to inspect or export a specific room.")
        return 0

    success = inspect_and_export_room(db, args.room, args.out)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
