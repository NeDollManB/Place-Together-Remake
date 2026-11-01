# Place Together 3.0 (Ultra-Fast Fork)

A high-performance custom plugin and synchronization engine for **Roblox Studio** that enables real-time collaborative game development (like Team Create) over local networks, VPNs (Radmin VPN, Hamachi, ZeroTier), or tunnels (ngrok, Porthole).

---

## 🚀 What's New in 3.0 (Fork Improvements)

### 1. Massive Performance Optimization (Event-Driven Engine)
- **O(1) Incremental Dirty Tracking:** Instead of scanning every property of every object across all 13 DataModel services on every tick (which caused severe frame drops on large maps), the plugin now uses event-driven `.Changed`, `.AttributeChanged`, `.DescendantAdded`, `.DescendantRemoving`, and `ScriptEditorService.TextDocumentDidChange` hooks combined with a lightweight rolling verifier.
- **Zero-Closure Property Reflection:** Eliminated thousands of per-tick anonymous closure allocations during property capture.
- **Single-Pass Apply:** Eliminated redundant full-place snapshots before and after applying remote deltas.
- **Fixed Critical Terrain Voxel Lag Bug:** Fixed the bug where empty air chunks around the camera were re-scanned (`ReadVoxelChannels` on 512,000 voxels) every 0.5 seconds forever.
- **Silent Fast Polling Server:** Suppressed synchronous Windows console I/O logging on high-frequency `/sync` and `/presence` requests.

### 2. Buttery-Smooth 60 FPS Sync & Live Dragging
- **6.6 Hz High-Frequency Sync & Presence (`0.15s`):** 3.3x faster tick rate with tiny delta packets.
- **60 FPS CFrame Interpolation (`RenderStepped`):** Remote `BasePart` and `Model` movements/rotations smoothly interpolate (`Lerp`) at 60 FPS instead of teleporting choppily.
- **Live Drag Transform Streaming:** When a peer selects and drags objects in Studio, live transform coordinates stream directly inside `/presence` packets for instant real-time visual feedback.
- **Local Drag Protection:** Actively dragged local objects are protected from remote echo jitter while the mouse button is held.
- **Frame-Budgeted Bulk Loading:** Joining large maps yields smoothly across frames so Roblox Studio never freezes.

### 3. Bug Fixes
- **Lossless Revision & Tombstone Engine:** Object versions are now unified with room revisions, and deleted objects are tracked via server tombstones (`r["tombstones"]`). Deletions and deltas are never lost—even across >200 revisions or server restarts.
- **Fixed `Ctrl+D` / Copy-Paste ID Collisions:** Duplicating Parts or Models in Studio now immediately detects `PT_ObjectId` collisions and assigns fresh GUIDs to duplicated hierarchies.
- **Full Toolbox Model Editing:** Removed the `isToolboxDescendant` restriction that previously prevented syncing edits, script changes, recolors, or deletions inside models inserted from the Toolbox.
- **Depth-Sorted Hierarchy Creation:** Deeply nested folders and models are sorted top-down by parent chain depth before parenting so children never fail with `"Parent unavailable"`.

### 4. Reliable Saving, Room Browser & Backup Time Machine
- **Smart Debounced Auto-Save:** The server automatically flushes changes to `data.json` within 2 seconds of any edit using atomic `.tmp` replacement and `.bak` corruption recovery.
- **Graceful Shutdown Hooks:** Closing the server console (`Ctrl+C` or closing the window) automatically flushes unsaved state to disk.
- **New `Saves & Rooms` Tab in Plugin:**
  - **Save & Create Backup Now:** Instant manual save + timestamped snapshot creation in `backups/`.
  - **Saved Rooms Browser:** View all rooms stored on the server and rejoin/resume any project in 1 click without needing a new invitation.
  - **Backup Time Machine:** Browse up to 25 automatic & manual snapshots per room and restore your map to any checkpoint in 1 click (all connected peers sync to the restored backup automatically).

### 5. Multiplayer Team Test (Co-op Playtest in Studio)
- **Playtest Together Directly in Studio:** No need to publish to Roblox or wait for slow Team Test cloud servers. Simply click **"▶ Start Team Test"** in the plugin or the toolbar button to playtest your map with friends!
- **Live 3D Avatars:** Automatically spawns real Roblox player avatars with your skin, hair, and overhead nametags.
- **Full Kinematic Physics & Controls:** WASD movement, Space to jump, LeftShift to sprint, obstacle climbing (stairs), floor collision against all parts and terrain, gravity, and void respawn.
- **60 FPS Peer Interpolation & Procedural Animation:** See your friends running, jumping, and falling in real time with smooth 60 FPS interpolation.
- **In-Game Chat Bubbles & HUD:** Press `/` or click the chat bar to type messages that pop up as overhead speech bubbles for everyone in the session.
- **Map Protection:** Test avatars reside in an un-archived folder (`PlaceTogetherTeamTest`), ensuring playtesting never interferes with or corrupts map synchronization.

### 6. Real-Time Collaborative Script Editing (3-Way Merge)
- **Simultaneous Co-Authoring:** Two or more developers can now edit the exact same Luau script at the exact same time without overwriting each other's work!
- **3-Way Line Diff & LCS Merge Engine:** Both server and plugin run an intelligent 3-Way diff & merge algorithm. If Developer A edits function `foo()` at the top of a script while Developer B edits `bar()` at the bottom, both changes cleanly merge together automatically without conflicts.
- **Live Script Document Integration (`ScriptEditorService`):** Applied remotely using `ScriptEditorService:UpdateSourceAsync` directly into open tabs in Studio without closing them.
- **Cursor & Caret Preservation:** When a friend's edits arrive while you are typing in the script editor, your cursor position and selection are smoothly preserved (`doc:SetSelection`) so you never lose your place or have your typing interrupted.
- **Live Co-authoring Banner & Peer Script Tracker:** See in real time who is currently editing which script and what line they are working on, right in the plugin UI!

### 7. Hardening & Developer Tooling (3.1)
- **Race-free saves:** the disk snapshot is now serialized while holding the room lock. Previously `/sync` handlers could mutate rooms mid-`json.dump`, which silently aborted saves under load.
- **Safe tombstone pruning:** deletions are dropped only once every connected peer has acknowledged them; the old code discarded an arbitrary half and could resurrect deleted objects for clients that were further behind.
- **Idle polls no longer bump revisions**, so peers stop re-downloading deltas that carry no information.
- **Optional server password** (`--secret` / `PT_SECRET`), guest session required for every room endpoint, owner-only backup restore, no wildcard CORS.
- **Bounded requests and connections:** 64 MB body cap, `30 s` socket timeout, queue depth for bursts.
- **Deterministic peer colours** (no random colour after a restart) and automatic cleanup of expired guests/invitations.
- **Plugin resilience:** the sync loop backs off exponentially (0.15 s → 5 s) while the server is unreachable instead of retrying 6.6 times per second.
- **`build.py`, tests and CI:** `PlaceTogether.rbxmx` is generated from `plugin_source.luau`, and a stdlib test suite covers sync, auth and persistence.

### 8. Collaboration Suite & High-Performance Upgrades (3.2)
- **📍 3D Spatial Beacons (Pings - "Look here!"):** Drop a glowing 3D beacon at your cursor or camera target with 1 click. Teammates see a pulsing sphere, vertical light beam, and a billboard showing your name, message, and live distance in studs, accompanied by a subtle audio chime.
- **💬 In-Studio Developer Team Chat:** A dedicated 4th tab **"Chat"** in the plugin UI allows teammates to communicate in real time in Studio edit mode with timestamps, sender color badges, unread notification dots, and incoming message chimes.
- **👥 Teammate Views, 1-Click Teleport & Camera Follow:** The **"Active Teammates"** card shows who is in the session, their current task (e.g. `Editing Script: Foo.luau:42`, `Building`, or `In Team Test`), with **"📍 Teleport"** (fly camera directly to peer's location and angle) and **"👁️ Follow"** (smoothly follow teammate's camera view).
- **⚠️ Selection Conflict Protection:** An instant visual warning banner alerts developers if a part or model they select is already being actively manipulated by another teammate.
- **🗜️ Transparent Gzip Network Compression:** The server automatically compresses HTTP payloads via `gzip` when requested by Roblox Studio (`Accept-Encoding: gzip`), cutting payload sizes by 70–90% on large maps and over VPNs.
- **🌐 Live Web Dashboard:** Opening `http://localhost:8787` in any web browser opens a sleek dark-themed live dashboard showing active rooms, revision counters, connected peers, uptime, and 1-click room snapshot downloads (`.json`).
- **🧰 Standalone Room Exporter & Inspector (`export_room.py`):** Inspect `data.json` or backups headlessly and export any room to standalone JSON without opening Roblox Studio.

---

## 📦 Getting Started

### 1. Install the Plugin
1. Open **Roblox Studio**, go to the **Plugins** tab, and click **Plugins Folder**.
2. Copy [`PlaceTogether.rbxmx`](PlaceTogether.rbxmx) into the Plugins folder (or copy all files into one folder).
3. Restart Roblox Studio if the plugin does not appear immediately on the toolbar.
4. Allow **HTTP Requests** and **Script Injection** when prompted by Roblox Studio.

### 2. Start the Server
Run [`StartPlaceTogetherServer.bat`](StartPlaceTogetherServer.bat).
- It will automatically use Python + [`server.py`](server.py) if Python is installed, or fall back to [`PlaceTogetherServer.exe`](PlaceTogetherServer.exe).
- Copy the displayed IP address (`[Radmin VPN]`, `[Hamachi]`, `[ZeroTier]`, `[LAN]`, or `[Localhost]`) and paste it into the **Settings** tab of the PlaceTogether plugin.

### 3. Host, Join, or Resume a Saved Room
- **Host a new or saved place:** Click **Start Hosting Current Place** on the **Together** tab (or invite a friend directly).
- **Resume an existing server room:** Open the **Saves & Rooms** tab and click any saved room on the server to join or resume it.
- **Create or restore backups:** Use **Save & Create Backup Now** on the **Together** tab or manage checkpoints in the **Saves & Rooms** tab.

### 4. Set a Server Password (recommended)
The server has no cloud accounts: anyone who can reach `http://<your-ip>:8787` can read and edit the room.

1. Start the server with a password: `StartPlaceTogetherServer.bat mypassword`, or set the `PT_SECRET` environment variable first.
2. Every participant types the same password into **Settings → SERVER PASSWORD (OPTIONAL)** in the plugin.
3. Requests without the password are rejected with `401`, and the server name/uptime endpoint stays the only public route.

Additional hardening that is now enforced:
- Every room endpoint requires an active guest session (the plugin registers one automatically).
- **Restoring a room from a backup is owner-only** — guests get `403`.
- Browser cross-origin access is disabled by default (pass `--cors-origin <origin>` if you build a web UI).
- Request bodies are size-capped (`64 MB`), and malformed `Content-Length` gets `400` instead of a server traceback.

---

## 🛠 Development

| File | Purpose |
| --- | --- |
| [`plugin_source.luau`](plugin_source.luau) | **Source of truth** for the Studio plugin |
| [`PlaceTogether.rbxmx`](PlaceTogether.rbxmx) | Generated Roblox model — do not edit by hand |
| [`server.py`](server.py) | Sync & persistence server |
| [`export_room.py`](export_room.py) | Standalone room inspector & snapshot exporter CLI |
| [`build.py`](build.py) | Rebuilds `PlaceTogether.rbxmx` from the Luau source |
| [`tests/test_server.py`](tests/test_server.py) | Server regression tests (stdlib only) |

After editing the plugin source, rebuild the model file:

```bash
python build.py            # write PlaceTogether.rbxmx
python build.py --check    # exit 1 if the .rbxmx is stale (used by CI)
```

Run the tests:

```bash
python -m unittest discover -s tests -v
```

CI (`.github/workflows/ci.yml`) runs the byte-compile check, `build.py --check` and the test suite on every push.

If you changed `server.py` and ship the standalone executable, rebuild it — otherwise the `.bat` launcher falls back to the old bundled binary on machines without Python:

```bash
pip install pyinstaller
pyinstaller --onefile --name PlaceTogetherServer server.py
```

