# Self-Service Seat Reset — Web Spec

สเปกสำหรับทีม web ทำปุ่ม "รีเซ็ตเครื่อง (Reset Device)" บนหน้า download `crgwwdc.shop/efootball`.
ฝั่ง **server (reroll-license :8787) ทำเสร็จแล้ว + deploy แล้ว** — เอกสารนี้คือ contract ให้ frontend เรียกใช้.

---

## 1. ปัญหา & กฎ

- key เช่าผูกกับ **HWID เครื่องแรก** ที่รัน (`seats=1` default). ลง Windows ใหม่ / เปลี่ยนเครื่อง → HWID ใหม่ → ขึ้น **"seat limit reached"** ใช้ไม่ได้.
- **Self-reset:** user ล้าง binding เองได้จากเว็บ
  - ครั้งแรก = ฟรี ทำได้เลยทุกเมื่อ
  - ครั้งถัดไป = ต้องรอ **10 วัน** (`RESET_COOLDOWN_SEC = 864000`)
- **Admin reset:** ไม่มี cooldown ทำได้ตลอด และ**ไม่กิน**โควตา cooldown ของ user (คนละช่องทาง)

## 2. ตัวตน (identity) — ใช้ **Discord**, ไม่ใช่ Google

key ผูกกับ `discord_id`. หน้าเว็บตอนนี้ใช้ Google (ef-session) — **reset ต้องใช้ Discord OAuth แยก** เพราะต้องได้ `discord_id` มา map หา key.

Flow: user กดปุ่ม reset → Discord OAuth (code flow) → เอา `code` ไปแลก `prod_access_token` ที่ server → ใช้ token เรียก reset.

### hwid param
ทุก endpoint รับ `hwid` (string). token ที่ออกจาก `/auth/discord/exchange` ผูกกับ `hwid` ที่ส่งตอนแลก และ `/reset/*` ต้องส่ง **hwid ตัวเดียวกัน** (verify ต้อง match). บนเว็บ **ใช้ค่าคงที่ไปเลย** เช่น `"web-reset"` — ค่านี้ไม่ได้เอาไป bind กับบัญชี มันแค่ผูก token ชั่วคราว (TTL 900s / 15 นาที). ย้ำ: ใช้ string เดียวกันทั้ง exchange + status + device.

## 3. CORS / การเรียก — เรียกผ่าน backend อย่าเรียกตรง

reroll-license อยู่ `https://140.99.254.10:8787` (cert self-signed, ไม่มี CORS). **อย่าให้ browser เรียกตรง.** ให้ backend `ckr-wwdc` (ที่มี `REROLL_API=https://127.0.0.1:8787` อยู่แล้วใน `efootball.py`) **proxy** ให้ — pattern เดียวกับ `upsert_key`/`ban`/`keyinfo` ที่มีอยู่.

เพิ่ม 3 proxy route ใน ckr-wwdc (ตัวอย่าง path ฝั่งเว็บ):
```
POST /api/efootball/reset/status   -> forward -> :8787 /reset/status
POST /api/efootball/reset/device   -> forward -> :8787 /reset/device
POST /api/efootball/auth/discord/exchange -> forward -> :8787 /auth/discord/exchange
```
(`httpx.AsyncClient(verify=False)` แบบที่ `efootball.py` ใช้อยู่)

---

## 4. API contract (reroll-license :8787 — เสร็จแล้ว)

### 4.1 แลก Discord code → token
`POST /auth/discord/exchange`
```json
{ "code": "<oauth code>", "redirect_uri": "<ต้องตรงกับที่ลงทะเบียนใน Discord app>", "hwid": "web-reset" }
```
→ `200 { "prod_access_token": "...", "discord_id": "...", "username": "..." }`
- redirect_uri ของหน้าเว็บ **ต้อง whitelist ใน Discord Developer Portal** (OAuth2 redirects) ของ client เดียวกับที่ server ตั้ง (`DISCORD_CLIENT_ID`).

### 4.2 เช็คสถานะ (ไว้โชว์ปุ่มก่อนกด)
`POST /reset/status`
```json
{ "prod_access_token": "...", "hwid": "web-reset" }
```
→ `200`
```json
{
  "discord_id": "...",
  "cooldown_sec": 864000,
  "keys": [
    { "key": "RRL-XXXX", "seats": 1, "seats_used": 1,
      "last_reset_at": 1795000000, "can_reset": false,
      "next_reset_at": 1795864000, "expires_at": 1799999999 }
  ]
}
```
- `can_reset=true` → ปุ่มกดได้ (ฟรี/พ้น cooldown)
- `can_reset=false` → โชว์ "รีเซ็ตได้อีกครั้ง <นับจาก next_reset_at>"
- `seats_used` = จำนวนเครื่องที่ผูกอยู่ตอนนี้

### 4.3 รีเซ็ตจริง
`POST /reset/device`
```json
{ "prod_access_token": "...", "hwid": "web-reset", "key": "RRL-XXXX" }
```
- `key` ใส่เมื่อ user มีหลาย key; ถ้ามี key เดียวไม่ต้องใส่ก็ได้
→ สำเร็จ `200 { "ok": true, "key": "...", "cleared": 1, "next_reset_at": 1795864000 }`
→ ติด cooldown `429 { "ok": false, "reason": "cooldown", "key": "...", "next_reset_at": ..., "cooldown_remaining_sec": ... }`

หลัง reset สำเร็จ บอก user ให้**เปิดโปรแกรมใหม่** — เครื่องใหม่จะผูกอัตโนมัติ.

### 4.4 Error codes
| code | หมาย | โชว์ user |
|---|---|---|
| 403 `discord required` | token หมดอายุ/ผิด/hwid ไม่ match | ให้ login Discord ใหม่ |
| 404 `no key for this account` | Discord นี้ไม่มี key เช่า | "ไม่พบคีย์เช่าในบัญชีนี้" |
| 409 `multiple keys` | มีหลาย key ไม่ได้ระบุ | ให้เลือก key (จาก /reset/status) |
| 429 `cooldown` | ยังไม่พ้น 10 วัน | "รีเซ็ตได้อีกครั้ง <next_reset_at>" |
| 503 `discord disabled` | server ปิด discord | ติดต่อแอดมิน |

### 4.5 Admin reset (ไม่มี cooldown) — สำหรับแอดมิน/ops
`POST /admin/reset/device`  header `X-Admin-Token: <token>`
```json
{ "key": "RRL-XXXX" }          // หรือ { "discord_id": "..." } (reset ทุก key ของ discord นั้น)
```
→ `200 { "ok": true, "keys": [...], "cleared": N }`
- ไม่แตะ `last_reset_at` → ไม่กิน cooldown ของ user. แอดมินทำได้ตลอด.
- (ตอนนี้แอดมินยิง endpoint นี้ได้เลย; จะผูกเป็นปุ่มในหน้า admin หรือคำสั่ง Discord bot ก็ได้)

---

## 5. UI requirement (หน้า download)

- ปุ่ม **"รีเซ็ตเครื่อง / Reset Device"** วางให้เห็นชัด **ตรงบริเวณปุ่ม Download** (user ที่ล็อกเครื่องจะหาเจอ)
- กดปุ่ม → ยังไม่ login Discord: เปิด Discord OAuth ก่อน
- login แล้ว: เรียก `/reset/status` → โชว์:
  - key + เครื่องที่ผูก (`seats_used`/`seats`)
  - ถ้า `can_reset` → ปุ่ม "รีเซ็ตเลย (ครั้งแรกฟรี / พร้อมแล้ว)"
  - ถ้าไม่ → ปุ่ม disable + "รีเซ็ตได้อีกครั้ง <วันที่จาก next_reset_at>"
- กดรีเซ็ต → `/reset/device` → สำเร็จโชว์ "สำเร็จ เปิดโปรแกรมใหม่ได้เลย" / 429 โชว์วันถัดไป
- เขียนอธิบายสั้นๆ: "ใช้เมื่อเปลี่ยน/ลงเครื่องใหม่ · ครั้งแรกฟรี ครั้งถัดไปรอ 10 วัน"

## 6. สิ่งที่ทีม web ต้องทำ
1. Discord OAuth (code flow) บนหน้าเว็บ + whitelist redirect_uri ใน Discord app (client เดียวกับ server)
2. proxy 3 route ใน ckr-wwdc → :8787 (ข้อ 3)
3. UI ปุ่ม + สถานะ ตามข้อ 5
4. ใช้ `hwid="web-reset"` คงที่ทั้ง flow

> server (reroll-license): endpoints `/reset/status`, `/reset/device`, `/admin/reset/device` + คอลัมน์ `reroll_keys.last_reset_at` — **deploy แล้ว ใช้งานได้ทันที**.
