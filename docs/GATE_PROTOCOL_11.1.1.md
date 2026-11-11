# eFootball Gate Protocol — 11.1.1 (FULLY CRACKED)

> Date: 2026-11-11. Reversed live off real A52 (arm64, rooted) via Frida + IDA MCP.
> Every piece below is **verified** (AES decrypt of real traffic succeeds; sign recomputed
> matches wire for every captured command). This supersedes the gRPC/6.0.1 notes in
> RE_PROGRESS.md — 11.1.1 uses plain HTTPS gate + custom AES, NOT gRPC.

## Transport
- `POST https://pes22-game.cs.konami.net/pes22/gate/gate_<CMD>.php`
- Headers:
  - `User-Agent: PES/1.0 (ANDROID 14; <user_code|0>; SM-A526B; <ts>; 6.1.1; ; 11.1.1; )`
  - `pes-custom-encrypt: AES256`
  - `Content-Encoding: gzip`   (really zlib, see body)
  - `Content-Type: application/x-www-form-urlencoded`
  - `Accept-Language: jp`
  - `Cookie: sign=<base64>;`   ← the request signature (see Sign)
- Body = raw bytes (the encrypted blob), NOT form-encoded despite the Content-Type.

## Body pipeline (encrypt)
```
plaintext_msgpack  ->  zlib.compress (deflate, header 78 da)  ->  AES-256-CBC  ->  IV(16) ‖ ciphertext
```
- **AES key (32 bytes, constant):**
  `43740981523cdc171e71de2ccab1a5a9b86f4b833196c55facd4bd25846c33f5`
- **Mode:** AES-256-CBC, PKCS7 padding.
- **IV:** random 16 bytes, **prepended** to the ciphertext. (Game uses a Mersenne-Twister
  seeded from /dev/urandom; any random 16 bytes work for us.)
- Final HTTP body = `IV(16) ‖ AES_CBC(key, IV, PKCS7(zlib(msgpack)))`.
- Decrypt = reverse: `zlib.decompress( AES_CBC_decrypt(key, body[:16], body[16:]) )`.

## Sign (Cookie: sign=)
```
sign = base64( HMAC_SHA256(hkey, body)[0:32]  ‖  LE32(v5)  ‖  LE32(v10) )    # 40 bytes -> b64
hkey = b"Xrq-RtAF_91MAE82|" + str(v5 ^ v10).encode()
```
- `body` = the full HTTP body (IV ‖ ciphertext).
- `v5`, `v10` = any 32-bit ints. Server recomputes `num = v5 ^ v10` from the LE tail and
  rebuilds `hkey`, so our choice is free as long as it's self-consistent.
- Verified: recomputed sign == wire sign for CMD_CHECK_STRING, CMD_CREATE_USER,
  CMD_GET_PRODUCT_LIST, CMD_SET_TRACKRECORD.

## Native anchors (libUE4.so, imagebase 0, 11.1.1 = binary 11.1.1/libUE4_1111.so)
- msgpack serializer (create_user fields): `0x75a10cc`; primitives add-key `0x2fcc828`,
  add-value `0x2fc9bac/0x2fcba44/0x2fcb670`.
- request orchestrators (serialize->encrypt->sign->send): `sub_7C0D648` (0x7c0d648),
  `sub_7C151A4` (0x7c151a4).
- gzip+encrypt wrapper: `sub_7C17680` (0x7c17680) -> gzip `sub_7C17580`, encrypt variants
  A `sub_7C0B228`(0x7c0b228)/B `sub_7C0BEC4`(0x7c0bec4).
- AES key setup (key passed as arg2 ptr / arg3 len): A `sub_7C0B6D0`(0x7c0b6d0),
  B `sub_7C0C3A8`(0x7c0c3a8).
- sign finalizer: `sub_7C17E5C` (0x7c17e5c); HMAC core (ipad/opad, SHA-256):
  `sub_7C18E58` (0x7c18e58).
- BoringSSL (system, libjavacrypto.so/libssl.so): SSL_write / SSL_read — TLS only
  (gate goes native->Java HTTP, body already encrypted). libUE4 RVA (11.1.1):
  SSL_write 0x80dcf94, SSL_read 0x80dcd60 (unused by gate, kept for reference).

## CMD_CREATE_USER plaintext (decrypted, real)
```
msgid: CMD_CREATE_USER
rqid: <random u32>
user_id: 0
session_id: ""
my_platform: ANDROID
s_keyword: <8 random-ish bytes>        # TODO: exact gen (in-body, used server-side)
hash: <32 hex>                          # TODO: HmacMD5-style device hash (see below)
client_version: "6.1.1"                 # NOTE: field value is 6.1.1, not the UA 11.1.1
platform: ANDROID
device: { device_identifier: <64 hex> }
lang: TH
region: REGION_ASIA
pcspec: NotImplement
user_eula_season: "2025"
user_eula_info: { eula_ip_country_a3: THA, eula_select_country_a3: THA, ... }
pes_user_info: { user_id:0, user_name: "efb_<rand>", is_deleted: NO, platform: IOS/ANDROID, ... }
cross_platform_option: NO
age: "2001-02-28"
os_version: "14"
model_name: SM-A526B
gpu_name: "Adreno (TM) 619"
soc_name: SM7225
survey_results: [{survey_id,answer_id}, ...]
user_age_kind: ADULT
is_rooting: YES
phy_mem_used_mib / available / app_storage_* / data_storage_*
payment_store_link_send_info: { country_code: "" }
kid_challenge_id: ""
is_savedata_existing: NO
```

## Flows (all WORKING — implemented in ../pes_api/)
- **Reroll:** `CMD_CHECK_STRING(name)` -> `CMD_CREATE_USER` -> response gives
  `{user_code, auth_code, pes_user_info.user_id, session_id}`. Reuse one valid
  `(device_identifier, hash)` pair; CREATE hash = `097679ed613607ef3d17b04e51784568`.
- **Daily:** `CMD_LOGIN(user_id, auth_code)` -> session; LOGIN hash = static
  `e8bbc34f90e46bbbbb6ef7a7a74df3ba`. Then `CMD_GET_MAILBOX_INFO` ->
  `CMD_RECV_PRESENT` claims all. Login registers the daily bonus -> mailbox.
- **CMD_RECV_PRESENT request** (lean commons — no hash/client_version/device):
  ```
  {msgid, rqid, user_id, session_id, my_platform:"ANDROID", s_keyword,
   present_list:[{present_id:<id>, breakdown_present_id:0}, ...], advertising_id:0}
  ```
- `s_keyword` is a device constant (same across requests); reused verbatim.

## Tutorial / "ready to use"
- eFootball 11.1.1 has **no forced playable tutorial** (FTUE is skippable; the real fresh-guest
  gate sequence has no match-result command). A rerolled account becomes lobby-ready after it
  **enters MyClub**: `CMD_GET_USEABLE_TEAM_LIST(list_type:"BASE_TEAM_SELECT")` then
  `CMD_SET_MYCLUB_ENTRY_INFO` with `base_team` + `squad_data{player_list, coach, squad_gameplan}`.
- The squad roster (base player ids + uniform numbers + formation) is **client-side game data**,
  not server-provided; reuse a captured team's squad (FC Barcelona 108 in `pes_api/entry_barcelona.json`).
- Every `gameplayer_id.serial` and `coach_id.serial` = `user_id * 1e9 + offset` — regenerate per
  account. `CMD_SET_MYCLUB_TUTORIAL`/`CMD_GET_MYCLUB_TUTORIAL` exist but are **not** called in the
  normal flow; not needed.

## Device fingerprint & the `hash` field (multi-device spread — SOLVED)
From `jp.konami.GetDeviceHash` (decompiled, base.apk):
- `getHashStr(msg,key) = hex_lower(HMAC_MD5(key.bytes, msg.bytes))`.
- `get(ctx, _) ` returns the raw device id: `Build.SERIAL (+ wlan0/eth0 MAC)` or, on modern
  Android (SERIAL = "unknown"), `Settings.Secure.ANDROID_ID` (16 hex). **Not** a hash.
- `getHashedWidevineId() = hex(MediaDrm widevine "deviceUniqueId")` = 32 bytes → **this is the
  `device_identifier` (64 hex)**. getHashStr is called from native, not dex.
- **The create `hash` (097679ed…) is NOT bound to device_identifier server-side** — verified:
  random `device_identifier` + this hash ⇒ NOERR; random hash ⇒ ERR_ONLINEPASS. So the hash is an
  app-integrity token (same for the client version), not a per-device value.

**Therefore multi-device spread needs no secret:** randomize `device_identifier` (32 random
bytes → 64 hex) + vary model/gpu/soc/os per account, reuse the valid app `hash`. Implemented in
`pes_api/flows.py::rand_device` / `create_user`; each account's device_identifier is saved in its
`.dat` and reused at login. Reproducing the native HMAC-MD5 secret is unnecessary.

## Account save format (output, matches ตัวอย่างผลลัพท์ ID / auth_new.dat)
```json
{"auth_code":"<40hex>","user_code":"ASxx...","user_id":<int>,"version":"2.0.0"}
```
