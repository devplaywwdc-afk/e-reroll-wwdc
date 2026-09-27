# eFootball Pure API Reroller — Handoff (continue on Mac M-series / arm64)

> **Date:** 2026-09-27
> **Why this doc:** capture is blocked on the Windows/MuMu box by **houdini** (x86→arm64
> translation) making Frida inline hooks fire only intermittently. Everything else is
> solved. On an Apple-silicon Mac (native arm64) the hooks work reliably — this doc plus
> `reroll_toolkit/` is everything needed to finish there.

---

## 1. Goal

Standalone headless reroller (`pure_api_reroll.py`) that talks the game's API directly
(no emulator/UI) to: create guest → login → claim/open free packs → grade cards → keep or
repeat. See `docs/TRAFFIC_MONITOR_AND_APK_PATCH_GUIDE.md` for the full protocol blueprint.

**The one remaining blocker:** our Python `CMD_CREATE_USER` is rejected with
`ERR_DATABASE / checkEulaVersion error:` because the request fields are stale (from client
**6.0.1**). The installed game is now **11.0.1** (versionCode `311000101`). We need the
**real 11.0.1 values** of three fields, then the Python reroller works:

- `hash`   (currently the static `e8bbc34f90e46bbbbb6ef7a7a74df3ba` from 6.0.1 — wrong now)
- `client_version` (was `"6.0.1"`)
- `user_eula_info.eula_version` (the 5 floats — server compares these exactly)

Capturing one real create_user request answers all three.

---

## 2. Protocol (confirmed, unchanged)

```
MsgPack dict  ->  base64  ->  protobuf envelope  ->  gRPC frame  ->  HTTP/2  ->  TLS (OpenSSL static in libUE4.so)
```
- Host: `pes22-game.cs.konami.net:443`
- Path: `/command_service.CommandService/CommandStream`
- Protobuf envelope: field1(0x0a)=cmd name, field2(0x10)=seq, field3(0x1a)=base64(msgpack), field4(0x22)="/"
- UA: `grpc-c++/1.26.0 grpc-c/9.0.0 (android; chttp2; gon)`

`reroll_toolkit/capture.py::decode_grpc()` decodes a raw frame end-to-end.

---

## 3. What is CONFIRMED working

- **Protocol/decoder** end-to-end (read-only cmds return `NOERR`; monitor decoded 133 live
  gRPC flows on 11.0.1 — see `monitor/exports/ckr_traffic_*.json`).
- **Root swap** pipeline is version-matched: `pesam_base.final2.apk` **is 11.0.1**
  (versionCode 311000101) and swaps cleanly (`SETUP_SWAP.bat` / `do_swap.sh`). Frida gadget
  is baked into `GameActivity.<clinit>` and loads at boot without tripping anti-tamper.
- **libUE4.so IDA offsets** (imagebase 0) verified against the running binary bytes:
  - `SSL_write`  @ `0x7FFA9B4`
  - `SSL_read`   @ `0x7FFA780`
  - `sub_75F2B40` create_user serializer @ `0x75F2B40` (byte-for-byte match with device)
  - CriHttp: open `0x8B0E4B4`, connect `0x8B0E854`, set_hdr `0x8B0E5C0`, set_body `0x8B0E70C`
- **create_user serializer field map** (offsets into the struct at `a1`, libc++ std::string):
  hash `0x1E8`, client_version `0x200`, platform `0x218`, device_identifier `0x230`,
  lang `0x248`, region `0x260`, user_eula_season `0x290`, eula `0x308`,
  eula_privacy_notice `0x320`, eula_myclub `0x338`, eula_japan_version `0x350`,
  eula_myclub_japan `0x368`, eula_country_age_kind `0x380`, eula_privacy_action `0x398`,
  user_id(int) `0x3B0`, user_name `0x3B8`. (Full list in `reroll_toolkit/hook_capture.js`.)
- **SSL_write/SSL_read signature** for version-robust scanning (they are thin wrappers,
  identical 80-byte body except 1 byte at offset 40: `8c`=write / `33`=read). Used by
  `hook_capture.js` so you don't depend on offsets surviving a game update.

## 4. What is BLOCKED (and why) — the houdini finding

On the Windows box the target is **MuMu (x86_64) running the arm64 game via houdini
translation**. Observed, reproducible:
- Frida attaches, gadget injects, `Interceptor.attach` reports success.
- **Low-RVA** hooks (`sub_2F92514` msgpack add-key @ `0x2F92514`) DO fire.
- The `SSL_write` hook fires in **steady state** (captured heartbeats) but **not** for the
  boot-time create_user; the `sub_75F2B40` hook never fired for create_user.
- Root cause: houdini caches/AOT-translates arm64 code; a Frida inline hook placed on the
  r-- source page doesn't reliably affect the executed translation. It's a **race**, not a
  logic bug — the scripts are correct.
- `CMD_CREATE_USER` fires **once at boot** when auth is absent → we keep losing the race.

**On native arm64 (your M5 Mac) there is no houdini** → the same hooks fire deterministically.

Also note: MuMu adb was only usable as root via `adb root` (Magisk denied `su` to shell),
and it needed `setenforce 0` (via `/sys/fs/selinux/enforce`). None of this applies on a
clean arm64 setup.

---

## 5. Finish it on Mac (native arm64) — step by step

Use ONE of: a real rooted Android device, or an arm64 Android emulator on the Mac
(Android Studio arm64 system image, or Genymotion/Corellium arm). No houdini.

### 5a. Prereqs
```bash
brew install --cask android-platform-tools     # adb
pip3 install frida-tools frida msgpack
adb devices                                     # confirm the device
```

### 5b. Get Frida onto the target (native arm64 frida-server — simplest, most reliable)
```bash
# match versions: host frida --version  == frida-server version
# download frida-server-<ver>-android-arm64 from github.com/frida/frida/releases
adb push frida-server-*-android-arm64 /data/local/tmp/frida-server
adb shell "su -c 'chmod 755 /data/local/tmp/frida-server; /data/local/tmp/frida-server &'"
frida-ps -U | grep -i football          # sanity: game is enumerable
```
(If you'd rather reuse the baked-gadget APK instead of frida-server, `pesam_base.final2.apk`
is 11.0.1 — install it or swap it in, and run `capture.py --gadget`. But on native arm the
frida-server + `--spawn` path below is the clean one.)

### 5c. Install the game (11.0.1) and reset guest to force a fresh create
```bash
# install from Play (keeps valid signature / PAD assets) OR sideload the same 11.0.1 splits.
# clear only auth so the next boot runs the guest-creation flow:
adb shell "su -c 'rm -f /data/user/0/jp.konami.pesam/files/SaveData/AUTH/* \
                        /data/user/0/jp.konami.pesam/files/SaveData/SYSTEM/*'"
```

### 5d. Capture (spawn = hooks live before boot-time create_user)
```bash
cd reroll_toolkit
python3 capture.py --spawn
# the game launches; walk the on-screen guest flow if it waits for UI:
#   Title -> accept EULA -> enter nickname -> confirm
```
Watch `capture.log`. On success you'll see:
```
===== CMD_CREATE_USER REQUEST (decoded) -> wire/xxxx_w_CMD_CREATE_USER.json =====
    hash                 = <REAL 11.0.1 hash>
    client_version       = 11.0.1
    user_eula_season     = ...
    eula_version         = {"eula":"..","eula_privacy_notice":"..","eula_myclub":"..",...}
```
Full payload: `reroll_toolkit/wire/*_w_CMD_CREATE_USER.json`. Cross-check struct dump:
`reroll_toolkit/create_dump.json`. Also grab `CMD_LOGIN` and `CMD_GET_EULA_AREA_LIST`.

### 5e. Fix the Python reroller
Copy the captured `hash`, `client_version`, and `eula_version` into
`scratch/test_create_fresh.py` (the `payload` in `step3_create_user`) and re-run. Diff every
field/type against the captured `wire/*_w_CMD_CREATE_USER.json`. Expect `checkEulaVersion
error` to disappear. Then wire up the chain
`CMD_CREATE_USER -> CMD_LOGIN -> CMD_GET_MYCLUB_PACK_LIST -> CMD_EXEC_LOTTERY` in
`pure_api_reroll.py`.

### 5f. If `hash` turns out to be dynamic (per-request), not static
If a second capture shows a *different* `hash` for the same client_version, it's computed
(HMAC/digest). Resolve statically in IDA:
- open the 11.0.1 `libUE4.so` (pull `/data/app/.../lib/arm64/libUE4.so`),
- `sub_75F2B40` reads `hash` from struct offset **488 (0x1E8)** — xref who WRITES that field
  before serialization; that writer is the hash derivation (likely MD5/SHA of a subset of
  fields + a salt). Reproduce it in Python.
(If two captures show the *same* hash for the same version, it's static — just hardcode it.)

---

## 6. reroll_toolkit/ contents
- `hook_capture.js` — Frida agent: signature-scan + hook SSL_write/SSL_read, plus direct
  create_user struct dump. Emits `send()` messages.
- `capture.py` — driver: `--spawn` (recommended), attach (default), or `--gadget`. Decodes
  gRPC→protobuf→base64→msgpack and writes `wire/*.json`, `create_dump.json`, `capture.log`.
- (older working captures for reference: `../monitor/exports/ckr_traffic_*.json`)

## 7. Known data captured so far
- Guest accounts were created during testing; their `user_id` / `auth_code` / `user_code`
  are kept LOCAL only (see `auth_new.dat` / `accounts/` on the working box — gitignored,
  not in this repo). The capture proved the create flow works and the save format holds
  `user_code`, `auth_code`, `user_id`, `version`.
- Device identifiers (adjust_id / android_id / gps_adid) appear in the analytics flow; also
  kept local.
- ZygiskFrida (MuMu only): v1.9.0, config `/data/local/tmp/re.zyg.fri/config.json`
  (targets array; set `app_name` + `enabled:true` + gadget `.config.so`). Not needed on Mac.

## 8. TL;DR for the next session
1. Native arm64 target + frida-server (versions matched).
2. Clear AUTH/SYSTEM, `python3 reroll_toolkit/capture.py --spawn`, do the guest flow.
3. Read `hash` / `client_version` / `eula_version` from `wire/*_w_CMD_CREATE_USER.json`.
4. Patch `scratch/test_create_fresh.py`, confirm `NOERR`, then finish `pure_api_reroll.py`.
5. Only if hash is dynamic: RE the writer of struct+0x1E8 in 11.0.1 `libUE4.so`.
