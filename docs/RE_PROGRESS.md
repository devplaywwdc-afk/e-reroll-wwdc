# eFootball 11.0.1 — RE Progress (transport, crypto, hash)

> **Date:** 2026-09-28. Continuation of `ENCRYPTION_FINDING.md`. This logs the deep RE done
> to reproduce the 11.0.1 gate protocol so `pure_api_reroll.py` can be built.

## Confirmed architecture (11.0.1)

- **Transport:** native (libUE4.so) builds each command → **AES-encrypts the msgpack body**
  (native) → hands the ciphertext `byte[]` to Java `HttpImpl.SendRequest(url, …, byte[], …)`
  which just POSTs it. Endpoint: `POST https://pes22-game.cs.konami.net/pes22/gate/gate_<CMD>.php`
  (the native object stores the leaf name e.g. `"CmdCreateUser.php"`; the URL adds `gate_` + `.php`).
  Content-Type `application/octet-stream`. NO gRPC. NO cert pinning (MITM decrypts TLS fine).
- **Body crypto:** AES, 16-byte block aligned (req+res all multiples of 16). Key is **native,
  NOT a plaintext string** (only 32-hex string in the binary is padding `999…`). So the key is
  a byte[] constant or derived — must be read from the encrypt function.
- **`hash` field:** computed by Java `jp.konami.GetDeviceHash` — **HmacMD5**:
  `getHash(msg, key) = hex( HmacMD5(key=key.getBytes(), msg.getBytes()) )` (32 hex chars).
  Device id source = Widevine `deviceUniqueId` (UUID edef8ba9-79d6-4ace-a3c8-27dcd51d21ed via
  `MediaDrm.getPropertyByteArray("deviceUniqueId")`) or `android_id` + wlan0/eth0 MAC.
  (Decompiled: `scratch/src/sources/jp/konami/GetDeviceHash.java`.) NOTE: still must confirm
  this is exactly the create_user `hash` and recover the HMAC key + exact input string.
- **client_version** = `"11.0.1"` (from APK versionName, injected Java-side).

## Native map (clean 11.0.1 IDB, imagebase 0)

- create_user command object constructors: **sub_75F26C0** (0x75F26C0) and **sub_75FCE8C**
  (0x75FCE8C). Both set cmd name `"CMD_CREATE_USER"` + endpoint `"CmdCreateUser.php"`, init
  request fields to `"NotImplement"`, install vtables.
- vtables: `off_97CD550` (its +0x10 = **sub_75F2B40**, the field serializer — same offset as
  6.0.1) and `off_97CD5E8` (several methods are stubs `return 1`).
- serializer **sub_75F2B40** builds the msgpack using the low-RVA primitives
  `sub_2F92514` (add-key: arg1 = key name) / `sub_2F8F898` (set value) / `sub_2F913C8`
  (set std::string). Field→struct-offset map (unchanged from 6.0.1, re-confirmed):
  hash `0x1E8`, client_version `0x200`, platform `0x218`, device_identifier `0x230`,
  lang `0x248`, region `0x260`, user_eula_season `0x290`, eula* `0x308…0x368`, etc.
- native→Java HTTP glue: **sub_7B3A684** (and sub_7B3C9B8) call `HttpImpl` methods by name
  (SetUserAgent/SetContentType `application/octet-stream`/AddRequestHeader/**SendRequest**).
  `criHttpRequest_SetRequestBody` @ **0x8b0e70c** (JNI-registered, used by CRI internal thread).
- The **native AES encrypt is between serialize and SendRequest** — not yet pinpointed (buried
  behind vtable dispatch). This is the one missing piece.

## Toolchain that WORKS (reuse these)

- **MITM capture:** `reroll_toolkit/konami_dump.py` (mitmdump addon). Proxy `settings put global
  http_proxy <host_ip>:8080`; captures every gate req/res body to `reroll_toolkit/konami_wire/`.
  Real encrypted create_user already saved: `konami_wire/016_req_gate_CMD_CREATE_USER.php.bin`.
- **IDA on clean 11.0.1** via HTTP: `reroll_toolkit/ida_mcp.py` + `idac.py` talk MCP-over-HTTP
  to the clean IDB instance (port **13338**). AVOID `find_regex` (full-strings scan crashes it);
  use `find`/`xrefs_to`/`decompile`/`func_query`/`get_bytes`. Clean binary:
  `binary 11.0.1/libUE4_1101_CLEAN.so` (the earlier `libUE4_1101.so` IDB was a truncated
  cat-pull → garbage; always `adb pull` a `cp`'d copy, never `su -c cat > file`).
- **jadx CLI** at `<scratch>/jadx/bin/jadx.bat` (v1.5.1). Full Java source decompiled to
  `<scratch>/src`. NOTE: jadx CLI crashes on Java 26 unless the external-plugins config is
  moved aside (`AppData/Roaming/skylot/jadx/config/plugins` → renamed to `plugins_bak`).
- capstone 5.0.7 + `<scratch>/elf2.py`/`xref.py` for ELF/ARM64 (slow for full .text scans).

## The two ways to finish (pick one)

1. **Read the plaintext at serialization (houdini-safe, NO AES key needed) — RECOMMENDED.**
   sub_75F2B40 adds every field via the LOW-RVA primitive `sub_2F92514`, which DID fire under
   MuMu houdini in earlier traces (unlike SSL/high-RVA hooks). Hook `sub_2F92514` (key) +
   `sub_2F913C8` (value) → log to a device file → do a UI-gated guest creation while warm →
   grep for `hash`/`device_identifier`/`eula_myclub_japan` and their values. That yields the
   real `hash` / `client_version` / `eula_version` directly. (Earlier attempt missed only
   because create fired at boot before hooks / scene-engine flood; a warm UI-gated create fixes
   it.) `scratch/hook_trace.js` is a starting point — filter to the create keys.
2. **Recover the native AES key** to decrypt `konami_wire/*.bin` offline. Continue tracing the
   command send method (the vtable method that runs sub_75F2B40 then encrypts obj+0x120 buffer)
   to the encrypt call and its key/IV/mode. Deterministic but slow via one-call MCP.

Either way, once the plaintext create_user is known, rebuild the reroller on the correct
transport: HTTPS POST `gate_<CMD>.php` with AES(msgpack) body + reproduce the `hash`.

## Environment notes
- MuMu is unstable: the game (jp.konami.pesam) has self-uninstalled several times; reinstall
  from Play (assets + valid signature) before any runtime capture. adb `127.0.0.1:5557`,
  `adb root` (adbd→root), SELinux permissive via `echo 0 > /sys/fs/selinux/enforce`.
  frida-server (`/data/local/tmp/fs64`) dies under Zygisk — use the **baked-gadget APK**
  (`pesam_base.final2.apk` swapped via `do_swap.sh`) for Frida instead.
</content>
