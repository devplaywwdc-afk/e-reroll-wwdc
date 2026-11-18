# eFootball Reroll — Licensing / Anti-Crack Architecture (Spec)

> สเปคสถาปัตยกรรม license + กัน crack สำหรับ **eFootball Reroll/Gacha/Daily** แบบ rental
> adapt จาก 2 ระบบที่ทำสำเร็จแล้ว: `WWDC_BOT_AD/docs/ANTI_CRACK_ARCHITECTURE.md` (APK) + `MAIN_WWDC_PC/mybot/ANTICRACK_ARCHITECTURE.md` (PC)
> สถานะ: **draft สำหรับอนุมัติก่อนลงมือ** — ยังไม่ build
> วันที่: 2026-11-11

---

## 0) ปรัชญาหลัก (เหมือนเดิม 5 ข้อ)

1. **Server-authoritative** — license/สิทธิ์/คีย์ถอดรหัส ตัดสินที่ server เท่านั้น client สะท้อนคำตัดสิน
2. **ของลับไม่อยู่ในเครื่อง** — gate crypto (AES key + HMAC seed) ไม่ฝังใน exe ดึงสด wrapped จาก server
3. **Secret ถอดใน native/compiled ไม่โผล่ Python** — คอมไพล์ module ลับเป็น `.pyd` (Nuitka) คีย์ไม่ออกจาก native
4. **Asymmetric > Symmetric** — Ed25519 (server ถือ private) → fake-server ปลอม grant ไม่ได้
5. **Defense-in-depth + ลงโทษหน่วงเวลา** — หลายชั้น + delayed silent ban (กันไม่ให้ cracker รู้จุดจับ)

**ยอมรับตั้งแต่ต้น:** ไม่มี 100% — เป้าคือ **ยกบาร์จนไม่คุ้มแกะ + สาวกลับหาคนปล่อย + บังคับออนไลน์ (server จับ/แบนได้)**

### ⚠️ ข้อแตกต่างสำคัญจากบอทเกม (อ่านก่อน)
- โปรโตคอล gate ของ Konami **ไม่ลับ** — อยู่ใน APK ใครก็ RE ได้ (เราก็ได้มาแบบนั้น) → เราไม่ได้ปกป้อง "โปรโตคอล" แต่ปกป้อง **ตัวโปรแกรม/บริการ rental ของเรา** (เครื่องมือสะดวก + ของพร้อมใช้ + กันแชร์คีย์)
- **แต่ reroll แข็งกว่าบอทเกมตรงนี้:** ทุก reroll ต้องยิง network สด + sign ด้วย key → บังคับออนไลน์โดยธรรมชาติ ไม่มี "offline farm บน cache" แบบบอทเกม

---

## 1) โมเดลภัยคุกคาม (Threat Model)

**ศัตรูจริง:** reseller แคร็กขาย (เคสคู่แข่งปัจจุบัน — .bat เปิดไม่จำกัด worker อ้าง "ใช้ server เลย crack ไม่ได้" ซึ่งเป็นมาร์เก็ตติ้ง: หลักฐานภาพคือยิงจาก **IP ลูกค้า** เต็มไปด้วย 500 throttle)

| # | Vector | วิธีโจมตี | ชั้นที่กัน |
|---|---|---|---|
| A | คัดลอก exe ไปแจก | ก๊อป exe | L0 (ต้อง session+key), L4 (brain เปล่า) |
| B | patch exe ข้าม license | NOP license check | L3 (M-bound key → wrong key) |
| C | dump key จาก RAM | hook จังหวะ key อยู่ RAM | L5 (decrypt in native), L6 (anti-debug), L8 (watermark สาวกลับ) |
| D | debugger/frida/injector | hook decrypt | L6 (threat fold เข้า M) |
| E | fake/MITM license server | ตั้ง server ปลอม mint สิทธิ์ | L1 (TLS SPKI pin) + L2 (Ed25519 grant) |
| F | key sharing / resell | เอาคีย์ไปแชร์หลายเครื่อง/หลายคน | L0 (HWID seats + Discord bind), L10 (anomaly scan) |
| G | replay session/grant | เล่นซ้ำ grant เก่า | nonce + short TTL + seq+HMAC |
| H | reverse Konami เอง | RE APK ดึง crypto เอง | **กันไม่ได้** (โปรโตคอลไม่ลับ) — moat = L7 rotation + L8 wm + บริการ |

---

## 2) โครงสร้างระบบ (identity 2 ชั้น: Discord + HWID)

```
┌──────────────────────────────────────────────────────────────────┐
│  PERSON ANCHOR          DEVICE ANCHOR         SERVER (VPS)         │
│                                                                    │
│  Discord OAuth2 ───────────────────────────▶  bind license        │
│  (discord_id = ตัวตนคน)                        ↕ discord_id        │
│                         HWID (MachineGuid                          │
│                         +board+cpu+disk) ───▶  seats table         │
│                         (device = เครื่อง)      1 discord → N hwid │
│                                                                    │
│  Loader exe ──── /session {key,hwid,discord_tok,M,build} ───▶     │
│  (thin, logic=0) ◀──── grant(Ed25519) + wrapped gate-key ────     │
│                                                                    │
│  ↓ decrypt brain ใน native → gate crypto อยู่ RAM                  │
│  reroll ยิง Konami gate จาก IP ลูกค้า (server ไม่กิน bw ตรงนี้)    │
│                                                                    │
│  heartbeat 60s (TTL 300s) ──────────────────▶ ต่อ lease / revoke  │
└──────────────────────────────────────────────────────────────────┘
```

**กฎ identity:**
- **1 Discord user → N HWID seats** (N ปรับได้ต่อ key/tier) — person คือ Discord, device คือ HWID
- Discord OAuth = พิสูจน์ "คนนี้ซื้อจริง" → bot ส่งคีย์ DM / role-gate ได้
- HWID = พิสูจน์ "เครื่องนี้" → กันคีย์แชร์ข้ามเครื่องเกินโควต้า
- ban ได้ทั้ง discord_id / key / hwid แยกกัน

---

## 3) brain ของ reroll = gate crypto (หัวใจ)

**"อะไรที่โปรแกรมขาดไม่ได้" = AES-256-CBC key + HMAC sign seed + schema/flow การ sign request** → นั่นคือ brain

```
brain (plaintext) = {
  aes_key, aes_iv_scheme,       # เข้ารหัส body gate
  hmac_seed,                    # sign request
  cmd_schemas,                  # CREATE_USER / USE_MYCLUB_AGENT / ... (field order/types)
  endpoint_map, client_version  # ยิงไปไหน + เวอร์ชันที่ gate รับ
}
→ pack → AES-256-GCM(K_brain, aad="RRB1") → brain.enc  (ship ใน installer, ciphertext ปลอดภัย)
```

- `K_brain` (สุ่ม 32B ต่อ build) **อยู่ server เท่านั้น** ปล่อยแบบ wrapped per-session
- ไม่มี session → ไม่มี K_brain → brain เป็น ciphertext → **sign request ไม่ได้ = reroll ไม่ได้เลย** (ไม่มี if ให้ patch)
- brain decrypt อยู่ **RAM only** ไม่เขียน disk

> **ทำไมเหมาะกว่าบอทเกม:** บอทเกมมี warm-offline ceiling (decrypt แล้ว patch รัน offline ได้) แต่ reroll **ทุก request = sign สด** → ถ้าอยากแน่นสุดใช้ oracle (§7) key ไม่เข้า client เลย

---

## 4) ชั้นป้องกัน (outer → inner) — แมปจาก 2 docs

```
L0  Server-auth + Discord/HWID binding + seats      (ฐานราก)
L1  TLS SPKI pin (client → เฉพาะ server เรา)
L2  Ed25519 operation grant (asymmetric)
L3  Integrity measurement M (วัด exe + threat probes) → fold เข้า unwrap key
L4  "empty exe" — gate crypto ไม่อยู่ใน exe (brain.enc ciphertext)
L5  Native-gated decrypt (คีย์ถอดใน .pyd ไม่โผล่ Python)
L6  Anti-debug / anti-hook (fold เข้า M)
L7  Per-build key rotation (เผา crack ทุก release)
L8  Per-account watermark (สาวกลับคนปล่อย)
L9  Native lease (CLOCK_BOOTTIME) — ฆ่า flag-flip
L10 Server anomaly scan + delayed silent ban
```

### L0 — Server-Authoritative + Discord/HWID
- login = **Discord OAuth2** → server ออก `prod_access_token` (Ed25519-signed, พิสูจน์ตัวตน+ownership)
- `/session` ต้องมี token + key + hwid → server ผูก (discord_id + key + hwid) ลง seats table
- heartbeat 60s ต่อ lease (TTL 300s) — lease หมด+offline → `killRun()` ล้างคีย์ใน RAM
- **seats:** 1 key → N hwid (กันแชร์)

### L1 — TLS SPKI Pin
- client ฝัง SPKI hash ของ cert server เรา → ต่อได้เฉพาะ server เรา (กัน MITM/fake server ชั้นนอก)

### L2 — Ed25519 Grant (asymmetric, กัน fake server ชั้นใน)
- server เซ็น `v1|discord|hwid|sid|build|iat|exp|nonce` ด้วย **private key** (อยู่ server คนเดียว)
- client ฝังแค่ `GRANT_PK` (public) → verify ใน native
- **TTL 300s + build-bound** → บังคับออนไลน์ถี่ + grant ข้าม build ใช้ไม่ได้
- **grant 2 gate พร้อมกัน:** (1) run-lease ทุก heartbeat (2) brain-unwrap ก่อนถอด gate key

### L3 — Integrity Measurement M (หัวใจ "ไม่มี if ให้ patch")
```
M = SHA256("RR1" || probe_files(exe) || probe_runtime())
probe_runtime() = _CLEAN ถ้า detect_threats()==[] else SHA256("threat:"+flags)
detect_threats() = debugger + injection + module_shadow + asset_manifest
```
- `K_wrap = HKDF(ecdh_shared, info="brain-wrap" || hwid || M)` → **M เพี้ยน = unwrap key ผิด = brain ไม่ถอด**
- ⚠️ **determinism:** M ที่ register บน server วัดตอน build (non-frozen, probe คืน []) → honest frozen client ต้องคืน [] ด้วย ไม่งั้น **ล็อกเอาต์ทุกคน** (ดู pitfalls)

### L4 — "Empty exe"
- gate crypto/schema **ไม่อยู่ใน exe** → ก๊อป exe ได้ก้อนเปล่า ไม่มี session = ไร้ค่า

### L5 — Native-Gated Decrypt
- unwrap + AES-GCM decrypt ทำใน `.pyd` (Nuitka) ทั้งหมด → key ไม่โผล่ Python layer → frida hook ชั้น Python ดูดไม่ได้

### L6 — Anti-Debug / Anti-Hook
- native เช็ก `TracerPid`, scan `/proc/self` (หรือ Windows: `IsDebuggerPresent`/`NtQueryInformationProcess` + loaded modules: frida/scylla/cheatengine…) → fold เข้า M
- **soft-rollout:** default แค่ report → server flip enforce ทีหลังเมื่อมั่นใจ FP=0
- ⚠️ allow-list runtime DLL จริง (เช่น `pythonXX.dll` ของ PyInstaller) ไม่งั้น false-positive

### L7 — Per-Build Rotation
- ทุก build → re-encrypt brain ด้วย K_brain ใหม่ + register `(M, K_brain, build_id)` → key ที่ถูกดึงตายทุก forced update

### L8 — Per-Account Watermark
- server ยัด `_wm = HMAC(WM_SECRET, "discord|build")[:24]` ลง config ที่ส่งแต่ละคน + log delivery
- ไฟล์หลุด → brute (discord_id, build) จาก delivery log → เจอคนปล่อย → revoke
- ⚠️ **ห้ามเปลี่ยน `WM_SECRET`** (เปลี่ยน = สาวกลับของเก่าไม่ได้)

### L9 — Native Lease (CLOCK_BOOTTIME)
- authority ของ lease อยู่ native → patch flag เดียวใน Python ไม่พอ; เลื่อนนาฬิกาไม่ช่วย

### L10 — Anomaly Scan + Delayed Silent Ban
- สแกน session ย้อน 7 วัน: **fanout** (hwid เกิน seats*3) / **geo** (≥4 ประเทศ) / **thrash** (เครื่องเดียว ≥12 token)
- review-only ก่อน → auto-ban เฉพาะ signal สุดโต่ง
- chain/tamper fail → `_ac_flag` คิว ban 6–72h (เงียบ, ไม่ refund, ไม่แบน Discord account)

---

## 5) แยก 3 exe (reroll / gacha / daily) — loader เดียวกัน

**Thin-loader ตัวเดียว ต่างแค่ `module_id`:**
```
eFootballReroll.exe   → module_id = "reroll"
eFootballGacha.exe    → module_id = "gacha"
eFootballDaily.exe    → module_id = "daily"
```
- loader logic = **0 บรรทัดธุรกิจ** — แค่: Discord login → session handshake → ขอ brain + engine module ของ `module_id` → exec ใน RAM
- entitlement ต่อ module: key มีสิทธิ์ `reroll` อย่างเดียวก็เปิด gacha ไม่ได้ (server กำหนด)
- เกม Konami อัปเดต → แก้ engine module ที่ server → exe ลูกค้าไม่ต้อง build ใหม่ (แค่ brain/module ใหม่ผ่าน forced-build)

**2 โหมดวางคีย์ (ต่อ module เลือกได้):**
- **A) brain-in-RAM (default)** — เร็ว เหมาะ reroll ที่ยิงพันๆ request
- **C) crypto-oracle** — key ไม่ออก server เลย client ส่ง body ให้ server sign; ช้า (+round-trip/req) ใช้เฉพาะ tier/op ที่ลับสุด

---

## 6) Flow หลัก

### (1) Activation
```
Discord OAuth2 login → ใส่ key → POST /session {key, hwid, discord_tok, M, build_id, module_id}
  server: verify discord_tok + cert/SPKI → check build==current (ไม่งั้น 426 update) →
          check entitlement(module_id) → bind seat(discord,key,hwid) → ECDH →
          K_wrap=HKDF(shared, hwid, M_expected) → wrapped=AESGCM(K_wrap, K_brain) →
          sign grant(Ed25519) → return {grant, sig, wrapped, personalized}
  client: verify grant sig + nonce + hwid + exp → K_wrap=HKDF(shared, hwid, MY_M) →
          K_brain=AESGCM.open(wrapped) [None=tamper→refuse] → decrypt brain.enc → gate crypto ใน RAM
```

### (2) Reroll (ยิง gate จาก IP ลูกค้า)
```
worker → ขอ sign body จาก brain (A: local native sign ด้วย key ใน RAM / C: /sign oracle) →
         ยิง pes22-game.cs.konami.net/gate/* จาก IP ลูกค้า → recv → เก็บ account local
heartbeat 60s → grant สด → renew native lease; revoked/tamper → killRun()
```

### (3) Forced Update
```
pinned /version → {build_id, sha256} ต่างจาก running → prompt (บังคับ, ปิดไม่ได้เพราะ server refuse brain ให้ build เก่า)
→ download /download/<build_id> → verify sha256 → swap in-place (ชื่อไฟล์เดิม) → relaunch
```

---

## 7) Server endpoints (เพิ่มบน VPS เดิม)

```
POST /api/reroll/v1/discord/callback   # OAuth2 → prod_access_token
POST /api/reroll/v1/session            # handshake → grant + wrapped K_brain
POST /api/reroll/v1/heartbeat          # lease renew / revoke / rules สด
GET  /api/reroll/v1/module/<module_id> # engine module (session-gated, encrypted)
GET  /api/reroll/v1/version            # forced-update feed (pinned)
GET  /api/reroll/v1/download/<build>   # exe (sha-verified)
POST /api/reroll/v1/sign               # (oracle mode C เท่านั้น) sign gate body
# admin
POST /admin/reroll/key                 # ออกคีย์ + ผูก discord + tier + seats + entitlements
GET  /admin/reroll/watermark/<wm>      # สาวกลับคนปล่อย
POST /admin/reroll/ban                 # ban discord/key/hwid
```

**Tables:** `reroll_users(discord_id, ...)`, `reroll_keys(key, discord_id, tier, seats, entitlements, ban_at)`, `reroll_devices(key, hwid, first_seen, ...)`, `reroll_builds(build_id, m_expected, k_brain, allowed)`, `reroll_sessions(sid, key, hwid, exp, sol_key)`, `reroll_deliveries(discord_id, build_id, wm, ts)`, `reroll_security_events`

---

## 8) VPS capacity (api.crgwwd.shop — 4c/8GB/120GB/500Mbps)

- ปัจจุบัน WWDC server กิน ~19% CPU / 5.83GB RAM → **เหลือพอสำหรับ control-plane ของ reroll** (handshake + heartbeat/60s + module download)
- **reroll ยิง Konami จาก IP ลูกค้า → VPS ไม่กิน bandwidth ตรงนั้น** (500Mbps เหลือเฟือ)
- ⚠️ **ถ้าใช้ oracle mode (C) ทั้งระบบ** → server sign ทุก request ทุก worker ทุกลูกค้า = พันๆ req/s ตอน mass reroll → 8GB/4c เริ่มตึง → **เลยเลือก A เป็น default**
- แยก process/port จาก WWDC server (คนละ systemd unit) กัน resource ปน

---

## 9) Build pipeline

```
release.py:   brain.enc = AESGCM(K_brain, pack(gate_crypto+schema))  → ship ใน installer
              build.blob = {build_id, M=measure(exe), K_brain}        → register server
compile:      module ลับ → Nuitka .pyd → PyInstaller onefile (3 exe ต่าง module_id)
register_exe: ทุก rebuild → exe hash เปลี่ยน → re-measure M → re-register (ไม่งั้น "brain check failed" ทุกคน)
deploy order: upload exe + sha-verify → แล้วค่อย flip current-build (กัน client ไล่ download ที่ยังไม่มี)
```

---

## 10) Pitfalls (ยกมาจากของจริง — ต้องรู้)

1. **Frozen-only probe false-positive = ล็อกเอาต์หมู่** — Nuitka loader รายงาน origin เป็นชื่อ `.py` → module-shadow check ที่ naive flag ทุก module → M เพี้ยน → ทุกคน "brain check failed". ทุกอย่างที่ fold เข้า M ต้อง **identical** ระหว่าง register (non-frozen) กับ frozen runtime
2. **ใส่ diagnostic ถาวร** — brain-check fail ให้ log build_id + M prefix + threat flags (ไม่งั้นแยก tamper/false-positive/stale ไม่ออก)
3. **หมุน grant key → อัปเดต signer ทุกจุด** (lease + feature) ไม่งั้นฟีเจอร์ออนไลน์ดับเงียบ
4. **canonical JSON** จุดเดียว shared — non-canonical = signature "สุ่มพัง"
5. **measure exe ไม่ใช่ run.py** — register M ผิดไฟล์ = brick ทุก exe
6. **fail-OPEN ถ้า gate ไม่เคย arm** — กัน legit พังตอน version ไม่ตรง สำคัญกว่าจับ cracker 100%
7. **ห้ามเปลี่ยน WM_SECRET / keystore / build secrets** — สำรองทุกอย่างที่หายแล้วจบ
8. **อย่าแบน Discord account** — แบนเฉพาะ key/hwid; เงียบ + หน่วง + ไม่ refund

---

## 11) ลำดับลงมือ (แนะนำ)

1. **L0** — Discord OAuth + HWID + seats + entitlements (ฐานราก) บน VPS เดิม
2. **L1 SPKI pin** + **L2 Ed25519 grant** (asymmetric ตั้งแต่แรก — อย่าใช้ HMAC symmetric)
3. **brain = gate crypto** → encrypt + **L5 native decrypt** + **L4 empty exe**
4. **thin-loader** 1 ตัว → 3 exe (reroll/gacha/daily) ต่างแค่ module_id
5. **L7 rotation + L8 watermark** (อายุ crack สั้น + สาวกลับ)
6. **L3 M + L6 anti-debug + L9 native lease**
7. **L10 anomaly scan** (review-only ก่อน → auto-ban เมื่อ FP=0)
8. **forced-update** ผ่าน pinned /version

> ทุกชั้น client = deterrent; ความจริงอยู่ที่ **server recompute/verify** เสมอ

---

## 12) สรุปจุดต่างจากคู่แข่ง (จุดขาย)

| คู่แข่ง (.bat) | ของเรา |
|---|---|
| .bat เปิดไม่จำกัด worker | worker ผูก seats (HWID) + lease TTL 300s |
| "ใช้ server เลย crack ไม่ได้" (มาร์เก็ตติ้ง) | Ed25519 + M-bound key + native decrypt (แกะไม่คุ้ม) |
| ไม่มี identity | Discord + HWID (ban/สาวกลับได้) |
| ไม่มี watermark | per-account wm → เจอคนปล่อย |
| ยิงจาก IP ลูกค้าเหมือนกัน (โดน throttle 500) | เหมือนกัน — แต่ขายที่ความสะดวก+ของพร้อม+กันแชร์ |
