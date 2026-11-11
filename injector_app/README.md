# eFootball Account Injector (standalone .exe)

ยัดไอดีที่ reroll มาเข้า **emulator บน PC** ให้เกม login ไอดีนั้น — รองรับ **batch หลายเครื่องพร้อมกัน**
(แยกจาก web GUI ใน `pes_api/gui` ซึ่งยังใช้ได้ตามปกติ)

## ใช้ยังไง
1. เปิด emulator (MuMu / LDPlayer / Nox / MEmu) ที่ลงเกม `jp.konami.pesam` + **เปิด Root** ไว้
2. รัน `dist\eFootballInjector.exe`
3. กด **🔄 สแกน emulator** → เลือกเครื่อง (Ctrl/Shift เลือกหลายเครื่อง)
4. เลือกไอดี:
   - **โฟลเดอร์ → 1 ไอดี/เครื่อง** — ยิงไอดีคนละตัวต่อเครื่อง (เรียงตามชื่อไฟล์)
   - **ไฟล์เดียว → ทุกเครื่อง** — ไอดีเดียวยิงทุกเครื่องที่เลือก
5. กด **💉 INJECT**

ไฟล์ไอดีใช้ได้ทั้ง `.txt`/`.dat` รูปแบบ reroll:
`{"auth_code":"...","user_code":"...","user_id":...,"version":"2.0.0"}`

## Build เป็น exe
```
build.bat
```
ได้ไฟล์เดียว `dist\eFootballInjector.exe` (stdlib + PyInstaller เท่านั้น)

## adb
หา adb อัตโนมัติตามลำดับ: `adb.exe` ข้างๆ exe → env `ADB_PATH` → PATH → ที่ติดตั้งของ MuMu/LDPlayer/Nox/MEmu
ถ้าหาไม่เจอ กดปุ่ม `…` เลือก `adb.exe` เองในแอป

## ทำอะไรบ้าง (ต่อเครื่อง)
`adb root` → force-stop เกม → backup ไอดีเดิม (โฟลเดอร์ `backups/`) → เขียน
`/data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat` → ล้าง `SaveData/SYSTEM/*`
(ตัด device-bind) → chown + restorecon → verify → เปิดเกม

> emulator ต้องเปิดเกมโหลด resource (~2GB) + สร้าง guest 1 ครั้งก่อน ถึง login ไอดีที่ inject ได้เต็ม
