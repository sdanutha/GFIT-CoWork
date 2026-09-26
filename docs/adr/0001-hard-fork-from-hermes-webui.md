# แยกทางถาวรจาก hermes-webui (hard fork)

GFIT-CoWork เอาโค้ดของ hermes-webui (nesquena/hermes-webui @ `c296673e`) มาเป็นฐาน และจะไม่ merge อัปเดตจาก Upstream อีก เหตุผลคือเราจะเปลี่ยนระบบ login ให้รองรับหลาย User และตัดหรือจำกัดฟีเจอร์หลายส่วน ซึ่งจะทำให้ merge กับ Upstream เกิด conflict แทบทุกครั้ง เราเก็บ git history เดิมไว้ทั้งหมด เพื่อให้ย้อนดูได้ว่าโค้ดเดิมเขียนแบบนี้เพราะอะไร และเก็บไฟล์ `LICENSE` (MIT) พร้อม copyright notice เดิมไว้ตามเงื่อนไขของ license

## Consequences

- bug fix และแพตช์ความปลอดภัยจาก Upstream จะไม่ตามมาเอง ถ้าต้องการต้องเลือกหยิบมาทีละ commit (`git cherry-pick`)
- เปลี่ยนชื่อแค่ส่วนที่ผู้ใช้เห็น และชื่อ package/repo เท่านั้น env var `HERMES_WEBUI_*` คงไว้ตามเดิม เพราะเปลี่ยนแล้วเสี่ยงสูงโดยไม่ได้ประโยชน์อะไร ส่วนชื่อที่เป็นของ Hermes Agent (`HERMES_HOME`, `hermes_cli`) ห้ามเปลี่ยน
