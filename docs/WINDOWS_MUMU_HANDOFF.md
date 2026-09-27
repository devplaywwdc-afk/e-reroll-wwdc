# eFootball Pure-API Reroller — Windows / MuMu Handoff (capture create_user under houdini)

> **Date:** 2026-09-27
> **Context:** The Mac (Android-Studio arm64 emulator) route was fully explored this
> session and is a **dead end** — see §1. Falling back to the **Windows / MuMu** box, where
> the game already runs. The only remaining problem there is the **houdini one-shot hook
> race** on the boot-time `CMD_CREATE_USER`. This doc is the plan to beat it.
>
> **Reading order:** `PROJECT_MAP.md` → `MAC_HANDOFF.md` → this file.

---

## 0. TL;DR

- Goal unchanged: capture **one** real 11.0.1 `CMD_CREATE_USER` request to read three fields
  (`hash`, `client_version`, `user_eula_info.eula_version`) and unblock the Python reroller.
- **New, important finding this session:** the `hash` is **dynamic** (computed at runtime),
  NOT a static string like the old 6.0.1 `e8bbc34f…`. So we *must* observe a live create_user
  — there is no constant to copy out of the binary, and any static-RE'd formula still needs a
  live capture to verify. See §3.
- On MuMu the game runs fine (it already served 133 live gRPC flows). The **only** blocker is
  that the inline Frida hook misses the single boot-time create_user. Fix options in §2,
  ranked. Try them in order.
- `reroll_toolkit/hook_capture.js` was **improved this session** (deterministic `base+RVA`
  hooks, offsets re-confirmed against the 11.0.1 binary). Use it as-is; it should already help
  on MuMu. See §2.0.

---

## 1. Why the Mac emulator route is dead (don't retry it)

Native arm64 Android-Studio emulator (`Pixel_7_CKR_2`, google_apis arm64, emulator 36.6):

- **GLES gate** — game needs OpenGL ES ≥ 3.1; the macOS host GL translator caps at ES 3.0.
  Fixed by enabling **Vulkan** (`~/.android/advancedFeatures.ini: Vulkan=on`) + forcing the
  app to the guest **ANGLE** driver (`settings put global angle_gl_driver_selection_pkgs
  jp.konami.pesam` / `…_values angle`) → ANGLE renders GLES over Vulkan/MoltenVK. Game boots.
- **RAM/disk** — bumped AVD to `hw.ramSize=8192`, `disk.dataPartition.size=24G` (had to
  `qemu-img resize` after deleting the qcow2 snapshot, then `-wipe-data` to actually grow the
  ext4). 22 GB free, 4 GB+ free RAM.
- **HARD WALL — integrity-gated download.** eFootball does a mandatory in-app asset download
  *before* the guest-creation flow. On the emulator it fails instantly at 0% ("Download
  Failed / sufficient storage space… network"). Ruled out storage (22 GB), RAM (8 GB),
  connectivity (WiFi validated, konami:443 reachable). Real cause: the game gates the download
  behind a **Play Integrity / DroidGuard** check via GMS. On retry the game makes **no** CDN
  DNS/connect at all — only Google/GMS sockets (CLOSE_WAIT) — i.e. it dies on the integrity
  precondition before it ever touches the CDN. A rootable emulator image can't pass Play
  Integrity by design; a Play-Store image can't be `adb root`'d (so no frida-server). A real
  rooted phone would pass (hardware attestation) — but none is available. **Route abandoned.**

Net: the emulator can render the game but can never reach guest creation. MuMu doesn't have
this problem (the game downloads and runs there).

---

## 2. The MuMu problem and how to beat it (do these in order)

**Problem (from `MAC_HANDOFF.md` §4):** MuMu on x86_64 runs the arm64 game via **houdini**
translation. Low-RVA inline hooks fire; `SSL_write` fires in **steady state** but the
**boot-time create_user** is missed; the serializer hook never fired for create_user. Root
cause is a translation-coherence race: houdini executes a block translated from the *un-hooked*
bytes on the first call; later calls pick up the patch. create_user fires ~once, early → lost.

### 2.0 Use the improved hook (already in repo)

`reroll_toolkit/hook_capture.js` now attaches `SSL_write`/`SSL_read` by **`base + 0x7FFA9B4`
/ `base + 0x7FFA780`** directly (offsets re-confirmed byte-for-byte in the 11.0.1 binary this
session) instead of a 100 MB `Memory.scanSync` that stalled and installed late. Hooks now come
up in ~90 ms at spawn ("all hooks installed (try 6)"). Late hook installation was almost
certainly *part* of the MuMu miss, so this change alone may fix it. The serializer struct
offsets (`hash 0x1E8`, `client_version 0x200`, `platform 0x218`, `user_eula_season 0x290`,
`eula 0x308`) were also re-confirmed present in the 11.0.1 serializer `sub_75F2B40`.

Run: `python capture.py --spawn` (frida-server matched to host `frida --version`).

### 2.1 PRIMARY: warm the hook, then trigger create_user by hand (cheapest)

create_user is reached through the UI: **Title → accept EULA → enter nickname → confirm.** The
EULA/country screens fire `CMD_GET_EULA_AREA_LIST` / `CMD_GET_COUNTRY_LIST` through the **same**
`SSL_write` first. Those warm the translation.

1. `python capture.py --spawn`, wait for "all hooks installed".
2. Walk to the Title/EULA screen and **stop**. Watch `capture.log` until you see
   `SSL_write CMD_GET_COUNTRY_LIST` / `CMD_GET_EULA_AREA_LIST` land in `wire/`. That is proof
   `SSL_write`'s translated block is hot and the hook is coherent.
3. **Only then** enter the nickname and confirm. create_user's `SSL_write` now hits a
   warm, hooked block → captured.

Do not rush step 3. The original miss was likely tapping through before the block was hot.

### 2.2 If create_user fires automatically at boot (no UI gate): network-gate it

If nothing user-driven precedes create_user (it auto-sends at boot when auth is absent):

1. Block the game's network *before* launch (Windows firewall outbound rule on MuMu, or pull
   the vNIC). Spawn with hooks. The boot create_user attempt fails and the client retries.
2. Confirm hooks are installed, and ideally that other SSL traffic has warmed the block.
3. **Unblock** the network. The retried create_user now sends through a warm, hooked
   `SSL_write` → captured.

### 2.3 ROBUST fallback: TLS MITM proxy (houdini-agnostic)

A network proxy captures the request at boot regardless of hook timing:

1. Point MuMu at a host proxy (mitmproxy / Fiddler / Charles).
2. Install the proxy CA into MuMu's **system** trust store (root: `adb root`, remount
   `/system`, drop the CA in `/system/etc/security/cacerts` with `chmod 644`). A user-store CA
   is not enough for a native OpenSSL client.
3. Launch, do the guest flow, read the create_user body from the proxy.

**Caveat — cert pinning.** If eFootball pins, the proxy sees a failed handshake. Then either:
(a) run a frida script alongside that neutralises the static-OpenSSL verify (hook the cert
verify path in `libUE4.so`), or (b) drop the proxy and go back to §2.1/§2.2 (the `SSL_write`
hook reads plaintext *before* encryption and never needs to defeat pinning — its only weakness
was timing, which §2.1/§2.2 address). Test the proxy **without** frida first; if the handshake
fails, pinning is present → use §2.1.

### 2.4 Extra frida robustness (optional, layer onto any of the above)

- Call `Interceptor.flush()` immediately after each `attach` (forces pending patches to apply
  while the process is still suspended at `--spawn`, before first execution).
- Keep `--spawn` (hooks placed while suspended) — never attach to an already-warm process for
  the first create_user.
- Also hook `CMD_CREATE_USER` at the **CriHttp** layer as a second net (open `0x8B0E4B4`,
  set_body `0x8B0E70C` — 6.0.1 offsets, re-verify on 11.0.1). Different translation path than
  the static OpenSSL exports; may win the race where `SSL_write` loses.

---

## 3. Key finding: the `hash` is dynamic (RE notes)

Static analysis of the pulled **11.0.1 `libUE4.so`** (160 MB) this session:

- The old 6.0.1 hash `e8bbc34f90e46bbbbb6ef7a7a74df3ba` is **not** in the 11.0.1 binary.
- The string `"11.0.1"` is **not** in `libUE4.so` at all → `client_version` is injected from
  the Java/APK side (`versionName`), value is simply **`"11.0.1"`**.
- The whole binary contains exactly **one** 32-hex-char string, and it is a run of `9`s
  (padding) — i.e. there is **no static hash constant**. The `hash` is therefore **computed at
  runtime** (32 hex chars ⇒ almost certainly MD5, hex-encoded).
- It is **not** a trivial digest of the version string (tested md5/sha1/sha256 of `6.0.1` and
  common salt combos against the known 6.0.1 hash — no match).

**Confirmed for the capture path (good news):** `sub_75F2B40` (create_user serializer) at RVA
`0x75F2B40` references struct offsets `0x1E8/0x200/0x218/0x290/0x308` — the field map still
holds on 11.0.1, so `hook_capture.js`'s struct dump reads `hash`/`client_version`/`eula`
correctly the moment create_user is serialized.

**Why static RE of the formula stalled:** there is **no direct `BL` to `sub_75F2B40`** anywhere
in `.text` (caller count 0) — UE4 dispatches it through a **vtable / indirect call**, so the
request *builder* (the code that writes the hash into `struct+0x1E8`) can't be found by a
simple call-xref scan. Finding it needs proper vtable analysis in IDA on an **11.0.1** database
(the `ida-libue4` release IDB is 6.0.1-era — re-analyse the pulled 11.0.1 `libUE4.so`).

**RE plan (only if live capture keeps failing):**
1. In IDA (11.0.1), locate the writer of `struct+0x1E8` (xref the vtable that contains
   `sub_75F2B40`; the ctor/init that populates the same struct writes 0x1E8).
2. That writer builds a 32-hex string — find the `MD5`/hex-encode it calls and its **input
   buffer** (which fields + any salt are concatenated before hashing).
3. Reproduce in Python; verify against one live-captured create_user (you still need one).

Given (3) requires a live capture to verify anyway, **§2 (capture on MuMu) is the priority**;
RE is the fallback, not a shortcut.

---

## 4. What to run on MuMu (quickstart)

```bash
# host frida must match the frida-server on the MuMu guest
frida --version
# native-arm frida-server OR the baked gadget (pipeline-artifacts) — MuMu already had a setup
adb connect 127.0.0.1:<mumu_port> && adb devices
adb root ; adb shell setenforce 0            # MuMu specifics from MAC_HANDOFF §4

cd reroll_toolkit
python capture.py --spawn                    # improved deterministic hooks
# -> wait for "all hooks installed"
# -> §2.1: sit on Title/EULA until CMD_GET_COUNTRY_LIST shows in wire/, THEN nickname->confirm
```

Success = `capture.log` prints the create_user block and `wire/*_w_CMD_CREATE_USER.json` +
`create_dump.json` appear with `client_version = 11.0.1` and a real `hash` / `eula_version`.
Then patch `scratch/test_create_fresh.py` and finish `pure_api_reroll.py` (MAC_HANDOFF §5e).

---

## 5. Session artifacts / state

- `reroll_toolkit/hook_capture.js` — **improved** (base+RVA deterministic SSL hooks; offsets
  re-confirmed on 11.0.1). Committed.
- 11.0.1 `libUE4.so` was pulled and analysed (findings in §3). Re-pull on the target with
  `adb shell su 0 cat <.../lib/arm64/libUE4.so>` if you need it for IDA.
- Mac emulator changes (Vulkan/ANGLE/RAM/disk) are local to that machine and irrelevant to
  MuMu — ignore them.
