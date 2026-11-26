eFootball Account -> .dat Converter
====================================

แปลงไฟล์บัญชี (.txt/.dat ของเรา) ให้เป็น .dat รูปแบบที่บอทแคปรูป adb ใช้ inject ได้

วิธีใช้ (เลือกอย่างใดอย่างหนึ่ง) — ใช้ efootball_dat_converter.exe ได้เลย ไม่ต้องลง Python
--------------------------------
วิธี A — โฟลเดอร์ input
  1. เอาไฟล์บัญชีทั้งหมดใส่ในโฟลเดอร์  input\
  2. ดับเบิลคลิก  efootball_dat_converter.exe
  3. ไฟล์ที่แปลงแล้วอยู่ในโฟลเดอร์  output\

วิธี B — ลากวาง
  ลากไฟล์ (หรือทั้งโฟลเดอร์) มาวางบน  efootball_dat_converter.exe
  ไฟล์ที่แปลงแล้วอยู่ในโฟลเดอร์  output\


รูปแบบไฟล์
----------
เข้า : {"user_code":"...","user_id":...,"auth_code":"...", ...}
ออก  : {"auth_code":"...","user_code":"...","user_id":...,"version":"2.0.0"}

ชื่อไฟล์ออก = <user_code>.dat


ต้องมี
------
- ไม่ต้องลงอะไรเลย ถ้าใช้  efootball_dat_converter.exe
- ถ้าจะรันจาก source (convert.bat / convert.py) ต้องมี Python บนเครื่อง


ปรับแต่ง
--------
ถ้าบอทต้องการ version อื่น เปิด convert.py แก้บรรทัด  VERSION = "2.0.0"
แล้ว build exe ใหม่ (ต้องมี pyinstaller):
    python -m PyInstaller --onefile --console --name efootball_dat_converter convert.py
