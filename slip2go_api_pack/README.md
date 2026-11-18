# slip2go API — source pack

โมดูลเชื่อม **slip2go** (ตรวจสลิป PromptPay + สร้าง QR) แยกออกมาจากเซิร์ฟเวอร์ CKR WWDC
เพื่อเอาไปแก้/ใช้บนเว็บ Windows ของมึง

## ไฟล์
- `slip2go.py` — โมดูลหลัก (self-contained)
- `USAGE_reference_from_main.py.txt` — ตัวอย่างวิธีเรียกใช้ (คัดจาก main.py ของเว็บปัจจุบัน) ไว้ดูอ้างอิง

## Dependency
- Python 3.9+
- `httpx`  → `pip install httpx`
- นอกนั้นใช้แค่ stdlib (`os`, `re`, `typing`) — **ไม่มี import ภายในโปรเจกต์อื่น** พึ่งตัวเองได้เลย

## ENV ที่ต้องตั้ง (ฝั่งเซิร์ฟเวอร์เท่านั้น ห้ามหลุด)
```
SLIP2GO_BASE_URL=https://connect.slip2go.com     # default มีให้แล้ว ไม่ตั้งก็ได้
SLIP2GO_API_SECRET=<secret จาก dashboard slip2go>  # ต้องตั้ง ไม่งั้น configured()=False
```
> secret ถูกอ่านจาก env เท่านั้น ไม่ได้ฝังในโค้ด — ปลอดภัยเวลา ship

## ฟังก์ชันที่เรียกใช้ได้ (public API)
| ฟังก์ชัน | ทำอะไร | async |
|---|---|---|
| `configured()` | เช็กว่าตั้ง secret แล้วหรือยัง | - |
| `generate_promptpay_payload(ewallet_id, amount_baht)` | สร้าง payload PromptPay (EMVCo + CRC16) ไว้ทำ QR เอง | - |
| `generate_qr_code(...)` | ขอ QR จาก slip2go | ✅ |
| `account_info()` | เช็กยอด/สถานะ account slip2go | ✅ |
| `verify_qr_code(ref, expected_baht, promptpay_code, ...)` | ตรวจสลิปจาก bank transfer ref | ✅ |
| `verify_image_base64(image_b64, expected_baht, ...)` | ตรวจสลิปจากรูป (base64) | ✅ |
| `check_condition(...)` | เช็กเงื่อนไข (ยอดตรง/ชื่อร้านตรง/ไม่ซ้ำ/ไม่โกง) | - |
| `looks_like_bank_trans_ref(value)` | เช็ครูปแบบ ref ก่อนยิง API (กันยิงมั่ว) | - |
| `shop_name_ok(name)` | เช็กชื่อผู้รับว่าตรงร้าน | - |

## ค่าคงที่ที่อาจต้องแก้ (ด้านบนไฟล์)
- `PROMPTPAY_CODE_DEFAULT` / `PROMPTPAY_NAME_DEFAULT` / `PROMPTPAY_NAME_TH_FULL` — **เลขพร้อมเพย์ + ชื่อบัญชีของมึง** (ตอนนี้เป็นของเดิม แก้เป็นของเว็บใหม่ได้)
- `_SHOP_NAME_NEEDLES` — คำที่ใช้ match ชื่อผู้รับในสลิป (ต้องตรงกับชื่อบัญชีใหม่)
- `_VERIFY_OK_CODES` / `_VERIFY_DUP_CODES` / `_VERIFY_FAIL_CODES` — code ที่ slip2go คืนมา (กันสลิปซ้ำ/โกง)

## กันสลิปซ้ำ (duplicate) — สำคัญ
โมดูลแยกแยะ code `200501` = สลิปซ้ำ และมี `_is_fraud` / `_is_duplicate` ให้ แต่
**การจำว่า ref ไหนเคยใช้แล้ว ต้องทำฝั่งเว็บเอง** (เก็บ bank_trans_ref ลง DB แล้วเช็กก่อนเครดิต)
— โมดูลนี้ตรวจกับ slip2go ให้ แต่ไม่ได้เก็บประวัติ ref ให้

## หมายเหตุ
- ทุกฟังก์ชันที่ยิง API เป็น `async` (ใช้ httpx.AsyncClient) — ถ้าเว็บใหม่เป็น sync ต้องห่อด้วย asyncio.run หรือแก้เป็น httpx.Client
- โยน `Slip2GoError(code, message, http_status)` เวลา error — จับไว้ตอบ user
