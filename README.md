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
