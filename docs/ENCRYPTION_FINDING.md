# eFootball 11.0.1 — Transport & Encryption Finding (major pivot)

> **Date:** 2026-09-27
> **How found:** MITM proxy (mitmproxy) on MuMu. This is the breakthrough that explains why
> every Frida-hook capture attempt failed. **Reading order:** `PROJECT_MAP.md` →
> `MAC_HANDOFF.md` → `WINDOWS_MUMU_HANDOFF.md` → this file.

---

## 1. The discovery

The 11.0.1 client does **NOT** use the gRPC `CommandStream` transport our Python reroller
targets. It talks to Konami over **plain HTTPS POST to per-command PHP "gate" endpoints**, and
**every request/response body is AES-encrypted** (application-layer, on top of TLS).

- Endpoint pattern: `POST https://pes22-game.cs.konami.net/pes22/gate/gate_<CMD>.php`
  (e.g. `gate_CMD_CREATE_USER.php`, `gate_CMD_GET_COUNTRY_LIST.php`, `gate_CMD_LOGIN.php`).
- **No** `/command_service.CommandService/CommandStream` traffic was observed at all in a full
  boot→create→login→MyClub session. The gRPC path is legacy/secondary (read-only cmds still
  answer on it, which is why `CMD_GET_COUNTRY_LIST` returned `NOERR` for us — but create/login
  go through the encrypted gate).
- **Every** captured body length is a multiple of 16 (req and res) → **AES block cipher**
  (CBC or ECB). Not compression (zlib/gzip fail; no magic bytes; high entropy). Examples:
  `gate_CMD_CREATE_USER` req 960 B, res 12224 B; `gate_CMD_GET_COUNTRY_LIST` req 144 / res 2880.

This is why all Frida approaches came up empty: `sub_75F2B40` (the gRPC create_user
serializer) is dead on 11.0.1, and the `SSL_write` gRPC hook never fired because **the game
isn't sending gRPC** — it POSTs encrypted blobs over ordinary HTTPS.

## 2. What MITM proved (and its limits)

- The game **honors the Android system HTTP proxy** (`settings put global http_proxy host:8080`).
- Konami TLS is **not pinned** — mitmproxy decrypts `pes22-game.cs.konami.net` fine **without**
  even installing a CA (Google/GMS endpoints DO pin and fail, but we don't need those).
- So MITM cleanly captures the HTTPS layer — but the **body is still AES-encrypted**. MITM
  alone is not enough; we need the AES key to read/forge command payloads.

## 3. Captured artifacts (local, gitignored)

`reroll_toolkit/konami_wire/` holds a full session's encrypted req+res blobs, including:
- `016_req_gate_CMD_CREATE_USER.php.bin` (960 B) — **the real 11.0.1 create_user request**
  (encrypted). Decrypt this to read `hash` / `client_version` / `eula_version`.
- `017_res_gate_CMD_CREATE_USER.php.bin` (12224 B) — its response (new account creds).
- plus GET_SERVER_ENV, GET_COUNTRY_LIST, GET_AGE_GATE_REQUIREMENTS, CHECK_STRING,
  GET_PRODUCT_LIST, LOGIN-path, MYCLUB_ENTRY, MAINMENU, etc. — a complete reference corpus of
  the encrypted protocol, all decryptable once the key is known.

## 4. Capture tool (reusable, houdini-agnostic)

`reroll_toolkit/konami_dump.py` — mitmproxy addon that saves every konami req/res body to
`konami_wire/` and tries to decode (currently sees ciphertext). Run:
```bash
mitmdump --listen-host 0.0.0.0 --listen-port 8080 -s reroll_toolkit/konami_dump.py
adb -s 127.0.0.1:5557 shell settings put global http_proxy <HOST_LAN_IP>:8080
# reset auth, create guest → blobs land in konami_wire/
adb -s 127.0.0.1:5557 shell settings put global http_proxy :0   # restore after
```
No frida / no CA needed. Works on MuMu (houdini) because it never touches the process.

## 5. THE critical next step — recover the AES key (RE in IDA on 11.0.1)

Everything now hinges on the AES key/IV/mode. Find it statically in the **11.0.1 `libUE4.so`**
(pull `/data/app/.../lib/arm64/libUE4.so`; the `ida-libue4` release IDB is 6.0.1-era — analyse
the 11.0.1 binary). Plan:
1. The gate POST goes through **CriHttp** (from the 6.0.1 map, re-verify on 11.0.1):
   `CriHttp::SetBody 0x8B0E70C`, `Open 0x8B0E4B4`, `Connect 0x8B0E854`, `SetHeader 0x8B0E5C0`.
   The body handed to `SetBody` is already ciphertext → the **encrypt call is just upstream**
   of whoever calls `SetBody` for a gate request.
2. Find the AES routine: search for AES constants (Rijndael S-box `63 7c 77 7b f2 6b 6f c5…`,
   or Te0/Td0 tables) in `libUE4.so`, or imported/stat-linked `AES_set_encrypt_key` /
   `EVP_EncryptInit` (BoringSSL is static). Xref its callers to the gate-body path.
3. Recover: key (likely static, or derived from device/version since create_user has no
   session yet), IV, and mode (CBC vs ECB — CBC if a fixed/prefixed IV; ECB if not). Confirm
   block size 16.
4. Write `decrypt(blob)` in Python; run it on `016_req_gate_CMD_CREATE_USER.php.bin`. Success =
   readable msgpack with `hash`, `client_version="11.0.1"`, `eula_version{…}`.

Note (from `WINDOWS_MUMU_HANDOFF.md` §3): `hash` is dynamic (computed, no static constant).
Decrypting the captured create_user reveals the actual `hash` value AND the plaintext layout;
if the reroller must forge it per-request, also RE the hash builder (writer of the request
field) — but first just decrypt the capture.

## 6. Reroller rebuild implication

The reroller must be rebuilt on the **correct transport**: HTTPS POST to `gate_<CMD>.php` with
an **AES-encrypted msgpack body** (not gRPC plaintext). Once `encrypt`/`decrypt` + the key are
in Python, the flow is: `gate_CMD_GET_SERVER_ENV → GET_COUNTRY_LIST → CREATE_USER → LOGIN →
GET_PRODUCT_LIST / pack open`. The captured `konami_wire/` corpus is the ground truth for every
command's field layout once decrypted.

## 7. TL;DR
1. 11.0.1 = HTTPS `gate_<CMD>.php` + AES-encrypted msgpack (NOT gRPC). MITM captures it; body
   still encrypted.
2. Real create_user request already captured (encrypted): `konami_wire/016_req_gate_CMD_CREATE_USER.php.bin`.
3. **Recover the AES key from 11.0.1 `libUE4.so` in IDA** (CriHttp SetBody upstream / AES S-box xref).
4. Decrypt the blob → read hash/client_version/eula → rebuild the reroller on the HTTP-gate+AES transport.
