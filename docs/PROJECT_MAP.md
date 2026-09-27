# eFootball Pure-API Reroller — Project Map (read me first)

Context for any AI/developer picking this up. What the project is, how the folder is laid
out, what each file does, and where to continue.

---

## 0. What this project is

Reverse-engineering **eFootball Mobile** (`jp.konami.pesam`, Unreal Engine 4 / `libUE4.so`)
to build a **headless account reroller** that talks the game's API directly (no
emulator/UI): create guest → login → open free packs → grade cards → keep or repeat.

**Current blocker (the whole reason work continues):** our Python `CMD_CREATE_USER` is
rejected `ERR_DATABASE / checkEulaVersion error:` because its `hash`, `client_version`, and
`user_eula_info.eula_version` are stale (client **6.0.1**); the game is now **11.0.1**.
Capturing ONE real 11.0.1 `CMD_CREATE_USER` request yields all three and unblocks everything.

**Why work moved to Mac (arm64):** the Windows box runs the arm64 game under MuMu's
**houdini** x86→arm translation, which makes Frida inline hooks fire unreliably, so we keep
losing the boot-time create_user. On native arm64 (Apple silicon) the hooks are
deterministic. Full analysis + steps: **`docs/MAC_HANDOFF.md`** (read that second).

**Reading order:** this file → `docs/MAC_HANDOFF.md` → `docs/TRAFFIC_MONITOR_AND_APK_PATCH_GUIDE.md`.

---

## 1. Protocol (one-paragraph recap)

`msgpack dict → base64 → protobuf envelope (f1=cmd, f2=seq, f3=b64 payload, f4="/") → gRPC
frame → HTTP/2 → TLS` to `pes22-game.cs.konami.net:443`
`/command_service.CommandService/CommandStream`. OpenSSL is static-linked inside `libUE4.so`.
Decoder reference: `reroll_toolkit/capture.py::decode_grpc()`.

Key `libUE4.so` offsets (IDA imagebase 0; verified against the running binary — but these are
the **6.0.1-era** IDB, re-verify on an 11.0.1 dump): `SSL_write 0x7FFA9B4`,
`SSL_read 0x7FFA780`, create_user serializer `sub_75F2B40 0x75F2B40`.

---

## 2. Directory / file guide

### `docs/` — documentation (start here)
- `PROJECT_MAP.md` — this file.
- `MAC_HANDOFF.md` — **the actionable plan** to finish capture on native arm64: env setup,
  frida-server, capture steps, how to patch the Python reroller, houdini findings.
- `TRAFFIC_MONITOR_AND_APK_PATCH_GUIDE.md` — deep protocol blueprint + how the patched APK
  was built (apktool decompile, smali `<clinit>` gadget inject, root-swap, resign).

### `reroll_toolkit/` — **the current, clean capture tool** (use this)
- `hook_capture.js` — Frida agent: signature-scans & hooks `SSL_write`/`SSL_read` (version-
  robust) + dumps the create_user serializer struct. Emits `send()` messages.
- `capture.py` — driver for the agent. Modes: `--spawn` (best; hooks from boot),
  default=attach, `--gadget` (baked-gadget on :27042). Decodes gRPC→protobuf→base64→msgpack,
  writes `wire/*.json` (per command) + `create_dump.json` + `capture.log`.
- `wire/` (gitignored) — decoded per-command captures land here at runtime.

### `monitor/` — the older, full "Charles-like" live traffic monitor (still works)
- `run.py` — one-shot launcher: sets adb-forward, auto-attaches a watcher, serves a web UI
  at `http://127.0.0.1:8770`. `START_MONITOR.bat` wraps this.
- `collector.py` — attaches Frida, injects `agent.js`, reassembles streams, holds flow store,
  auto-reconnects. `ensure_frida_server()` is gadget-aware (no external frida-server needed).
- `agent.js` — Frida agent hooking `SSL_write`/`SSL_read` (hardcoded 6.0.1 offsets + prologue
  check) and CriHttp; resolves libUE4 base from `/proc/self/maps`.
- `http2.py` — HTTP/2 + HPACK reassembly. `decode.py` — gRPC/protobuf/msgpack decode.
- `server.py` — FastAPI backend. `export.py` — export flows. `web/` — dashboard UI
  (`index.html`, `app.js`, `style.css`). `requirements.txt` — monitor deps.
- `exports/` (gitignored) — saved capture sessions; the Sep-23 files hold real decoded 11.0.1
  steady-state traffic (heartbeat/watch) — useful structural reference (contain session
  tokens, hence not in git).

### Root — reroller + device pipeline
- `api_reroll.py` — API-based reroller (WIP; the one to finish once create_user is fixed).
- `reroll_bot.py` — legacy ADB/UI-automation reroller (screen taps, slow). `card_detector.py`
  — grades pulled cards from screenshots. `players_db.json` — card/player reference DB.
  `reroll_config.json` — reroller thresholds/settings. `RUN_REROLL_BOT.bat` — runs the bot.
- `analyze_flows.py` — quick analysis over captured flow JSON.
- **Device pipeline (for the baked-gadget / non-root path):**
  `SETUP_SWAP.bat` + `do_swap.sh` — root-swap the patched `base.apk` + gadget into the
  installed app. `START_MONITOR.bat` — launch the monitor. `RESET_GUEST_ID.bat` — back up &
  clear auth to force a fresh guest. `gadget.config.json` — frida-gadget listen config.

### `scratch/` — exploratory RE scripts (mostly one-off; low priority to read)
Dozens of throwaway probes (`read_disasm_*`, `check_*`, `dump_*`, `scan_*`, `inspect_*`,
`find_*`, `hook_*`). The ones that matter:
- `test_create_fresh.py` — **the create_user/login test to fix** with the captured 11.0.1
  values (this is where the blocker gets closed).
- `test_login.py`, `test_multi_login.py`, `test_pack_list.py`, `test_eula_area.py`,
  `test_user_eula_info.py` — per-command API tests.
- Frida hooks that informed `reroll_toolkit/` (superseded, kept for reference):
  `hook_ssl2.js`, `hook_createuser.js`, `agent_boot.js`, `hook_trace.js`, `probe.js`, and
  their `frida_*.py` drivers.
- `*_resp.json`, `*_live.log`, `wire/` (gitignored) — captured responses/logs.

---

## 3. GitHub Releases (large binaries — not in git)
- **`ida-libue4`** — `libUE4_efootball.i64` (1.9 GB): IDA database (6.0.1-era analysis;
  function names/offsets reference). `gh release download ida-libue4 -R Evasi0m/efootball-reroller`
- **`pipeline-artifacts`** — `pesam_base.final2.apk` (11.0.1, gadget baked) +
  `libfrida-gadget.so` (arm64): for **non-rooted** capture. On rooted arm64 skip these and use
  frida-server. `gh release download pipeline-artifacts -R Evasi0m/efootball-reroller`

## 4. Secrets / data kept LOCAL (gitignored, never pushed)
`auth_new.dat`, `accounts/`, `saved_accounts/`, all `*.json` traffic dumps, `*.dat`,
`*.keystore`, screenshots (`*.png`), memory dumps (`maps.txt`, `*.bin`). Account creds and
session tokens live only on the working machine. (Code/docs were scrubbed of literals.)

## 5. Next action (TL;DR)
1. Native arm64 target + matching frida-server (see `MAC_HANDOFF.md` §5).
2. Clear AUTH/SYSTEM → `python3 reroll_toolkit/capture.py --spawn` → do the guest flow.
3. Read `hash` / `client_version` / `eula_version` from `reroll_toolkit/wire/*_w_CMD_CREATE_USER.json`.
4. Patch `scratch/test_create_fresh.py`; confirm `NOERR`; finish `api_reroll.py`.
5. If `hash` is per-request dynamic: RE the writer of struct offset `0x1E8` in 11.0.1 `libUE4.so`.
