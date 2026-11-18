# Spec — หน้า Genlink KONAMI (crgwwdc.shop)

สำหรับทีมเว็บ/AI ฝั่งเว็บ นำไปทำต่อได้เลย. เอกสารนี้อธิบาย (ก) วิธีเรียกใช้ src ที่แนบมาใน
โฟลเดอร์ `genlink_web/` และ (ข) หน้าเว็บ + หลังบ้าน + หน้า Admin ที่ต้องสร้าง.

> **โทน UI/UX:** ออกแบบตามหน้า `crgwwdc.shop/efootball/` ที่มีอยู่แล้ว ใช้โทนสี/ฟอนต์/การ์ด
> ชุดเดียวกัน. รองรับทั้ง **mobile และ desktop**. หัวใจของ UX คือ **สื่อสารเข้าใจง่าย ไม่ซับซ้อน**
> ผู้ใช้ทั่วไปกดไม่กี่ปุ่มก็ได้ลิงก์. อย่าใส่ศัพท์เทคนิคที่ลูกค้าไม่จำเป็นต้องรู้ (user_id,
> auth_code, session ฯลฯ ซ่อนไว้หลังบ้าน).

---

## 0. สรุปสั้น

- **การ์ดทางเข้า** บนหน้าหลัก `https://www.crgwwdc.shop/efootball` → คลิกเข้า `/genlink` (ดูข้อ 5.0).
- **หน้าใหม่ แยกจากหน้าหลัก:** `https://www.crgwwdc.shop/efootball/genlink`
- **สิ่งที่หน้านี้ทำ:** รับข้อมูลบัญชี eFootball (reroll) → สร้างลิงก์ผูกบัญชี KONAMI ID (my.konami.net)
  ที่มีอายุ ~20 นาที → ลูกค้าเอาลิงก์ไปเปิดเพื่อผูกบัญชีเข้ากับ KONAMI ID ของตัวเอง.
- **ราคา:** 0.5 token ต่อ 1 ลิงก์ (Admin ปรับได้ + ตั้งโปรโมชันได้).
- **ไม่ต้องเชื่อม Discord** สำหรับการดึงลิงก์ (ใช้แค่ระบบ token ของเว็บ).

---

## 1. Backend integration — เรียก src ที่แนบมา

โฟลเดอร์ `genlink_web/` คือ engine สำเร็จรูป. Backend เรียกฟังก์ชันเดียว:

```python
from genlink import generate_link

r = generate_link(
    user_id=2052761546,                 # int
    auth_code="5c088c47a7674bba...",    # str
    device_identifier="ae2efa4a...",    # str, optional (มี default ให้)
    lang="th",                          # optional
)
```

คืน dict เสมอ (ไม่ throw บน gate error):

```jsonc
{
  "ok": true,                 // true เฉพาะเมื่อ result == "NOERR" และมี url
  "url": "https://my.konami.net/th/account-link/...",
  "expires": 1763400000,      // unix timestamp ที่ลิงก์หมดอายุ (~20 นาทีข้างหน้า)
  "expires_in": 1200,         // วินาทีที่เหลือ ใช้ทำ countdown
  "link_status": "...",
  "result": "NOERR",
  "error": "..."              // มีเฉพาะตอน ok == false
}
```

**กฎสำคัญ:** ถ้า `ok == false` → งาน error → **คืน token ที่หักไว้** แล้วแสดงข้อความสั้น ๆ.

`generate_link()` ทำ login 1 ครั้ง + ยิง gate 1 คำสั่ง ใช้เวลา ~1–3 วิ. ให้รันใน worker/async task
(เช่น Celery / RQ / background job) ไม่ใช่รันตรงบน request thread เพื่อรองรับหลายคนพร้อมกัน.

ติดตั้ง: `pip install -r genlink_web/requirements.txt` (requests, msgpack, pycryptodome).

---

## 2. Input — รองรับ Code / ไฟล์ / Folder (ข้อ 1)

ลูกค้าต้องส่ง "ข้อมูลบัญชี" เข้ามา. 1 บัญชี = 1 record ที่มีอย่างน้อย `user_id` + `auth_code`
(และ `device_identifier` ถ้ามี). รองรับช่องทางเหล่านี้:

### 2.1 แบบ Code (วาง/พิมพ์)
ช่อง textarea ให้วาง record. รองรับ 2 รูปแบบ ให้ parser เดาอัตโนมัติ:

- **JSON record** (รูปแบบที่ระบบ reroll บันทึก):
  ```json
  {"auth_code":"5c088c...","user_code":"ASCV342175879","user_id":2052761546,"version":"2.0.0"}
  ```
- **คอมแพกต์** `user_id:auth_code` หรือ `user_id:auth_code:device_identifier`
  ```
  2052761546:5c088c47a7674bba3c24e0f31a9884450082549d
  ```

วางหลายบรรทัดได้ = หลายบัญชี (batch).

### 2.2 แบบไฟล์เดี่ยว — `.txt` / `.dat`
อัปโหลดไฟล์ `.txt` **หรือ `.dat`** 1 ไฟล์. เนื้อไฟล์คือ record เดียว (JSON) หรือหลายบรรทัด.
นี่คือไฟล์บัญชีที่ระบบ reroll / emulator ออกให้ตรง ๆ:
- `.txt` เช่น `ASCV342175879.txt`
- `.dat` เช่น `auth_new.dat` / `online_user_id_data.dat` (ไฟล์ AUTH ของเกม)

**สำคัญ: `.dat` มีเนื้อ JSON เหมือน `.txt` เป๊ะ** — แค่คนละนามสกุล. parser อ่าน bytes → decode utf-8
→ parse JSON ตัวเดียวกัน. ห้ามเดาว่า `.dat` เป็น binary; อ่านเป็นข้อความได้เลย.
ตัวอย่างเนื้อ `.dat`:
```json
{"auth_code":"d0deee0d45e8dcc34df46c1901df5c07f4ed2096","user_code":"ASPY338882055","user_id":2005459643,"version":"2.0.0"}
```

### 2.3 แบบ Folder / หลายไฟล์ (batch)
อัปโหลดทั้งโฟลเดอร์ (หรือ `.zip`, หรือเลือกหลายไฟล์พร้อมกัน) — คละ `.txt` / `.dat` ได้.
ระบบ parse ทุกไฟล์เป็นรายการบัญชี แล้วคิดเงินตามจำนวนลิงก์ที่ดึงจริง (ดูข้อ 3).

### Parser — normalize ทุกแบบให้เป็น
```json
{ "user_id": 2052761546, "auth_code": "5c088c...", "device_identifier": "ae2efa4a..." | null }
```
- ข้าม record ซ้ำ (user_id เดียวกัน) ในชุดเดียว.
- record ที่ parse ไม่ได้ / ขาด user_id หรือ auth_code → mark เป็น invalid, **ไม่คิด token**, แจ้งผู้ใช้.

---

## 3. ราคา + การหัก/คืน token (ข้อ 2, 4)

### 3.1 ราคา
- **ราคาตั้งต้น: 0.5 token ต่อ 1 ลิงก์.**
- หน่วยราคาเป็น **token**. จำนวนลิงก์ = จำนวนบัญชีที่ valid ในคำสั่งนั้น.
- ยอดที่คิด = `จำนวนลิงก์ × ราคาต่อลิงก์ (หลังโปรโมชัน)`.

### 3.2 หัก token ก่อน (hold)
ลำดับต้อง**แบบนี้เป๊ะ** เพื่อกันการใช้ token ฟรีหรือหักซ้ำ:

1. คำนวณยอด = จำนวนบัญชี valid × ราคาต่อลิงก์ (หลังโปรโมชัน ณ เวลานั้น).
2. ตรวจ balance พอไหม. ไม่พอ → บล็อก ไม่เริ่มงาน แจ้งยอดที่ต้องมี.
3. **หัก (hold) token ทั้งก้อนก่อนเริ่มดึงลิงก์** — บันทึก transaction สถานะ `held`.
4. ดึงลิงก์ทีละบัญชีผ่าน `generate_link()`.
5. ปิดงาน:
   - บัญชีไหน `ok == true` → ยืนยันหัก token ของลิงก์นั้น (`charged`).
   - บัญชีไหน `ok == false` (error/หมดอายุ login/gate ล่ม) → **คืน token เฉพาะลิงก์ที่ fail** กลับเข้า balance, บันทึก `refunded`.
6. ถ้าทั้งงาน fail หมด → คืนเต็มจำนวน.

> **ย้ำ (ข้อ 4):** ต้องคืน token ทุกกรณีที่ลิงก์นั้นดึงไม่สำเร็จ. token ถูกหักจริงเฉพาะลิงก์ที่
> ออกสำเร็จเท่านั้น.

### 3.3 Admin ปรับราคา + โปรโมชัน (ข้อ 2)
Admin ตั้งค่าได้:
- **ราคาต่อลิงก์** (token) — แก้ได้ตลอด. มีผลกับคำสั่งที่เริ่มหลังแก้เท่านั้น (คำสั่งที่ held ไว้แล้วใช้ราคาเดิม).
- **โปรโมชัน** มีช่วงเวลาเริ่ม–สิ้นสุด (ตั้งเวลาได้) และชนิดส่วนลด:
  - **แบบ % (percent):** ลดราคา X% เช่น -50% → 0.25 token/ลิงก์.
  - **แบบบาท (amount):** ลดเป็นจำนวนคงที่. รองรับหน่วย token หรือ บาท
    (ถ้าเป็นบาทให้ตั้งค่า `token_baht_rate` ไว้แปลงเป็น token).
  - ราคาหลังลดห้ามต่ำกว่า 0.
- โปรโมชันที่ active (อยู่ในช่วงเวลา) จะถูกใช้คำนวณราคาอัตโนมัติ, และต้องโชว์บนหน้า genlink
  (เช่น แบนเนอร์ "โปรลดราคา 50% ถึง 20 พ.ย.").

ตัวอย่าง config:
```jsonc
{
  "price_per_link_tokens": 0.5,
  "token_baht_rate": 10,               // 1 token = 10 บาท (ใช้แปลงโปรแบบบาท)
  "promotion": {
    "active": true,
    "label": "เปิดตัว ลด 50%",
    "discount_type": "percent",        // "percent" | "amount"
    "value": 50,                       // 50 (%) หรือจำนวนลด
    "amount_unit": "token",            // ใช้เฉพาะ discount_type == "amount": "token" | "baht"
    "starts_at": 1763300000,
    "ends_at":   1763900000
  }
}
```

---

## 4. Lifecycle ของลิงก์ — countdown 20 นาที + ขอใหม่ (ข้อสำคัญ)

ต่อ 1 ลิงก์ที่ดึงสำเร็จ:

- แสดง **นาฬิกานับถอยหลัง** จาก `expires_in` (เช่น `19:58`, `19:57`, ...). หมดเวลาที่ `expires`.
- ปุ่ม **"ขอลิงก์ใหม่"**:
  - **ก่อนหมดอายุ:** ปุ่มถูก **disable**. ต้องรอให้ลิงก์เดิมหมดอายุก่อนถึงขอใหม่ได้
    (gate ออกลิงก์ใหม่ให้เฉพาะหลังลิงก์เก่าหมดอายุ — บังคับกติกานี้ที่หลังบ้านด้วย).
  - **หลังหมดอายุ:** ปุ่ม enable. กดแล้วเรียก `generate_link()` ใหม่ **ไม่คิด token เพิ่ม**.
- การขอใหม่หลังหมดอายุ = ฟรี (ผูกกับ transaction เดิมของบัญชีนั้น). บันทึกเป็น `re-issue` ใน log
  แต่ **ไม่สร้างรายการเก็บเงินใหม่**.

หลังบ้านเก็บต่อบัญชี/ต่อ transaction: `last_url`, `last_expires`, จำนวนครั้ง re-issue.
กติกาเช็คก่อนออกใหม่: `now >= last_expires` เท่านั้น.

---

## 5. หน้า Genlink (`/efootball/genlink`) — flow + UI (ข้อ 3, 6)

หน้าแยกเดี่ยว ไม่รวมกับหน้าหลัก. **ไม่ต้องเชื่อม Discord** (ข้อ 6) — ใช้แค่ระบบ login/token ของเว็บ
ที่มีอยู่ (ล็อกอินเว็บเพื่อรู้ว่าใครมี token เท่าไร ก็พอ; ไม่ต้องผูก Discord OAuth สำหรับฟีเจอร์นี้).

### 5.0 การ์ดทางเข้า บนหน้าหลัก `/efootball`
เพิ่ม **การ์ด 1 ใบ** บนหน้า `crgwwdc.shop/efootball` (โทน/สไตล์เดียวกับการ์ดอื่นในหน้านั้น)
ลิงก์ไป `/efootball/genlink`:

- **Header:** `Link Konami เรท {price}/id` — โดย `{price}` **ต้องดึงจาก API ไม่ใช่ค่าตายตัว**.
  ดึงราคาปัจจุบัน (หลังโปรโมชัน) จาก `GET /efootball/genlink/price` ทุกครั้งที่โหลดหน้า.
  วันไหน admin เปลี่ยนเป็น 0.6 / 0.3 / 0.1 ตัวเลขบนการ์ดเปลี่ยนตามทันที.
  ตัวอย่างแสดงผล: `Link Konami เรท 0.5/id`.
- **คำอธิบาย (กระชับ):** `ใช้ Code / txt / dat เดี่ยวหรือทั้งโฟลเดอร์ ได้ Link Konami เชื่อม ID เกมทันที`
- ถ้ามีโปรโมชัน active → ติด badge เล็ก ๆ (เช่น ราคาขีดฆ่าเดิม + ราคาโปร) ใช้ข้อมูลจาก endpoint เดียวกัน.

`GET /efootball/genlink/price` (public, ไม่ต้องล็อกอิน) คืน:
```jsonc
{
  "price_per_link": 0.5,          // ราคาปัจจุบันหลังโปร (ตัวเลขที่โชว์บนการ์ด/header)
  "base_price": 0.5,              // ราคาตั้งต้นก่อนโปร (ไว้ขีดฆ่า)
  "promotion": { "active": true, "label": "ลด 50%", "ends_at": 1763900000 } | null
}
```

> ห้าม hardcode `0.5` ใน frontend. ทั้งการ์ดหน้าหลัก และ header ในหน้า genlink อ่านจาก endpoint นี้.

### Flow ผู้ใช้ (ให้ง่ายที่สุด)
1. เข้าหน้า → เห็นราคาปัจจุบัน/โปรโมชัน + balance token ของตัวเอง.
2. เลือกวิธีใส่บัญชี: **วาง Code** / **อัปโหลด .txt** / **อัปโหลดหลายไฟล์/โฟลเดอร์** (แท็บหรือปุ่มสลับ).
3. ระบบ parse → โชว์สรุป: "พบ N บัญชี, ราคา = N × 0.5 = X token" (มี valid/invalid แยก).
4. กด **"ดึงลิงก์"** → หัก token → progress ทีละบัญชี.
5. ผลลัพธ์: การ์ดต่อบัญชี แสดง
   - สถานะ (สำเร็จ/ล้มเหลว — ถ้าล้มเหลวบอก "คืน token แล้ว"),
   - ปุ่ม **คัดลอกลิงก์** + ปุ่ม **เปิดลิงก์**,
   - **นาฬิกานับถอยหลัง**,
   - ปุ่ม **ขอลิงก์ใหม่** (disable จนกว่าจะหมดอายุ).
6. งานเสร็จโชว์สรุป: ออกสำเร็จกี่ลิงก์, คืน token กี่อัน, token ที่ใช้จริงเท่าไร.

### UI notes
- ใช้การ์ด/ปุ่ม/สีชุดเดียวกับ `crgwwdc.shop/efootball/`.
- Mobile: การ์ดเรียงแนวตั้ง, countdown ตัวใหญ่อ่านง่าย, ปุ่มคัดลอกกดง่าย.
- Desktop: ตารางหรือ grid การ์ด.
- อย่าโชว์ user_id/auth_code ของบัญชีเต็ม ๆ บน UI (โชว์ย่อ เช่น `ASCV…879` พอ).
- ข้อความ error เป็นภาษาคน: "ดึงลิงก์ไม่สำเร็จ บัญชีนี้ล็อกอินไม่ได้ (คืน token แล้ว)".

---

## 6. หน้า Admin — Transaction ดึง Link (ข้อ 5)

เพิ่มแท็บ/หน้า **"Genlink Transactions"** ใน Admin. ต้องเห็น **ทั้งหมด**:

ตารางรายการ (ต่อ 1 คำสั่งดึง และ/หรือ ต่อ 1 ลิงก์ — แนะนำ 2 ระดับ: header = คำสั่ง, ขยายดูลิงก์ย่อย):

| ฟิลด์ | ความหมาย |
|------|----------|
| `tx_id` | รหัส transaction |
| `user` | ผู้ใช้ที่ดึง (ชื่อ/บัญชีเว็บ) |
| `created_at` | เวลาเริ่มคำสั่ง |
| `input_type` | code / txt / folder |
| `accounts_total` | จำนวนบัญชีที่ส่งเข้ามา |
| `links_ok` | ดึงสำเร็จกี่ลิงก์ |
| `links_failed` | ล้มเหลวกี่ลิงก์ |
| `price_per_link` | ราคาต่อลิงก์ ณ เวลานั้น (หลังโปร) |
| `tokens_held` | token ที่หักไว้ตอนเริ่ม |
| `tokens_charged` | **token ที่จ่ายให้เว็บจริง** (เก็บจริง) |
| `tokens_refunded` | token ที่คืน |
| `promotion` | โปรที่ใช้ (ถ้ามี) |
| `status` | held / done / partial-refund / refunded |

กดเข้า tx → เห็นรายละเอียดรายลิงก์:

| ฟิลด์ | ความหมาย |
|------|----------|
| `account` | user_code ย่อ |
| `result` | NOERR / error code |
| `url` | ลิงก์ที่ออก (คัดลอกได้) |
| `issued_at` / `expires_at` | เวลาออก / เวลาหมดอายุ |
| `reissue_count` | ขอใหม่ฟรีกี่ครั้ง |
| `charged` / `refunded` | สถานะเงินของลิงก์นี้ |

สรุปด้านบน (ช่วงเวลาเลือกได้): จำนวนลิงก์รวม, token เก็บจริงรวม, token คืนรวม, จำนวน user,
และ error rate. ฟิลเตอร์ตาม user/เวลา/สถานะ, export CSV.

---

## 7. Data model (แนะนำ)

```
genlink_tx
  id, web_user_id, created_at, input_type,
  accounts_total, links_ok, links_failed,
  price_per_link, tokens_held, tokens_charged, tokens_refunded,
  promotion_snapshot (json), status

genlink_link
  id, tx_id, user_id, user_code, device_identifier,
  result, url, issued_at, expires_at, reissue_count,
  charged (bool), refunded (bool)

genlink_settings   (single row หรือ key/value)
  price_per_link_tokens, token_baht_rate, promotion (json)
```

**อย่าเก็บ auth_code เป็น plaintext ถาวร** — ใช้ตอนดึงลิงก์เสร็จแล้วทิ้ง หรือเข้ารหัสถ้าจำเป็นต้องเก็บ
สำหรับ re-issue. เก็บเท่าที่ re-issue ต้องใช้ (user_id, auth_code, device_identifier) และจำกัดอายุ.

---

## 8. API endpoints (แนะนำ)

```
GET  /efootball/genlink/price             // public: ราคาปัจจุบันหลังโปร (ป้อนการ์ด + header)

POST /efootball/genlink/quote
  body: { input_type, payload }           // parse + คำนวณราคา, ยังไม่หัก token
  resp: { accounts:[{user_code, valid}], links, price_per_link, total_tokens, promotion }

POST /efootball/genlink/run
  body: { input_type, payload }           // หัก token (hold) + เริ่ม job
  resp: { tx_id }

GET  /efootball/genlink/tx/{tx_id}        // poll สถานะ + ลิงก์ + countdown (expires_at)

POST /efootball/genlink/link/{link_id}/reissue   // ขอใหม่ฟรี (เช็ค now>=expires_at)
  resp: { url, expires_at } | 409 ถ้ายังไม่หมดอายุ

-- admin --
GET  /admin/genlink/tx                     // list + filter + export
GET  /admin/genlink/tx/{tx_id}
GET/PUT /admin/genlink/settings            // ราคา + โปรโมชัน
```

---

## 9. Edge cases

- **Login fail / gate maintenance** → `ok=false` → คืน token, แจ้ง "ลองใหม่ภายหลัง".
- **บัญชีผูก KONAMI ID ไปแล้ว** → `link_status` บอกสถานะ; ยังออก url ได้หรือไม่ขึ้นกับ gate.
  ถ้า `ok=false` ให้คืน token ตามปกติ.
- **Batch ใหญ่** → ทำเป็น job + progress, ยิง gate ทีละบัญชี (มี retry ใน `GateClient` อยู่แล้ว),
  เว้นช่วงเล็กน้อยกันโดน throttle ต่อ IP.
- **ขอใหม่ก่อนหมดอายุ** → ตอบ 409 + เวลาที่เหลือ, ปุ่มฝั่ง UI disable อยู่แล้ว.
- **token แข่งกันหัก (race)** → หัก token แบบ atomic (lock/transaction DB) ตอน hold.

---

## 10. Checklist ส่งมอบ

- [ ] การ์ดทางเข้าบนหน้า `/efootball`: header `Link Konami เรท {price}/id` (price จาก API, ไม่ hardcode) + คำอธิบายกระชับ.
- [ ] หน้า `/efootball/genlink` แยก, โทนตามเว็บเดิม, mobile+desktop.
- [ ] Input: Code / `.txt` / `.dat` / Folder(หลายไฟล์, คละ .txt/.dat). `.dat` = JSON เหมือน .txt.
- [ ] คิดราคา 0.5 token/ลิงก์, Admin ปรับราคา + โปรโมชัน (%/บาท, ตั้งเวลา).
- [ ] หัก token ก่อน + คืน token เมื่อ error.
- [ ] Countdown 20 นาที + ขอใหม่ฟรีหลังหมดอายุ (บล็อกก่อนหมดอายุ).
- [ ] Admin Transaction: เห็นทุก user, รายละเอียด, เวลา, token เก็บจริง.
- [ ] ไม่ต้องเชื่อม Discord สำหรับดึงลิงก์.
- [ ] เรียก engine ผ่าน `genlink_web/genlink.generate_link()`.
