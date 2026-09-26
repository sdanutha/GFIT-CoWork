# หนึ่ง User ต่อหนึ่ง Profile ใน Hermes Agent ตัวเดียวที่ใช้ร่วมกัน

แต่ละ Team มี Deployment ของตัวเอง (Hermes Agent 1 ตัว + GFIT-CoWork 1 ตัว + API key 1 อัน) ข้างในรองรับ 30–50 User โดยแต่ละ User login ด้วยชื่อ Profile ของตัวเองเป็น username และถูกล็อกให้เข้าถึงได้เฉพาะ Profile นั้น (ต่อยอดจากกลไก `bound_profile` ที่มีอยู่แล้ว) เราเลือกแบบนี้แทนการแยก container ต่อ User เพราะดูแลง่ายกว่ามาก และใช้โครงสร้าง Profile ที่ Hermes มีอยู่แล้ว

## Consequences

- **นี่ไม่ใช่ขอบเขตความปลอดภัย** ทุก Profile รันด้วย OS user เดียวกัน User จึงสั่ง agent ให้อ่านไฟล์ของ Profile อื่นได้ รวมถึง `.env` ของ Profile อื่น เรา **ตั้งใจยอมรับ** ข้อนี้ เพราะ User ใน Team เดียวกันไว้ใจกันได้ และทุก Profile ใช้ API key เดียวกันอยู่แล้ว ถ้าจะมีคนนอก Team มาใช้ ต้องกลับมาตัดสินใจเรื่องนี้ใหม่
- ฟีเจอร์ระดับ server (terminal, git push/pull, extensions, self-update, shutdown, logs, YOLO mode, การเลือก Workspace นอก Profile) เปิดให้เฉพาะ Admin เพราะฟีเจอร์เหล่านี้ทำให้ทะลุออกนอก Profile ได้ง่าย
- ยังไม่เคยทดสอบว่า GFIT-CoWork process เดียว (`ThreadingHTTPServer`) รับ User พร้อมกัน 30–50 คนได้ ให้เริ่มใช้กับ 5–10 คนแล้ววัดผลก่อนขยาย
