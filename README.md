# Speaking Bot

Telegram bot: ko'p darajali (multilevel) imtihon uchun Speaking va Writing'ni AI bilan baholaydi.

## Render'ga joylash

1. Render → **New → Blueprint** → shu GitHub repo'ni tanlang (`render.yaml` avtomatik o'qiladi).
   Qo'lda yaratsangiz: **Web Service**, Root Directory `speakbot`, Build `pip install -r requirements.txt`,
   Start `python main.py`, Health Check Path `/health`.
2. **Environment** bo'limida to'ldiring:

| O'zgaruvchi | Majburiy | Izoh |
|---|---|---|
| `BOT_TOKEN` | ha | @BotFather bergan token |
| `GROQ_API_KEY` | ha | https://console.groq.com — Whisper (ovoz→matn) va standart baholash |
| `ADMIN_IDS` | tavsiya | Admin Telegram ID'lari, vergul bilan (ID: @userinfobot) |
| `EXAM_LANGUAGE` | yo'q | `tr` (standart) yoki `en` |
| `GRADER_PROVIDER` | yo'q | `groq` (standart) yoki `claude` (`ANTHROPIC_API_KEY` ham kerak) |
| `TURSO_DATABASE_URL` | tavsiya | `libsql://<baza>.turso.io` — berilsa, ma'lumotlar Turso'da saqlanadi (deploy/restartda o'chmaydi, disk kerak emas) |
| `TURSO_AUTH_TOKEN` | Turso bilan | Turso token (`turso db tokens create <baza>`) |
| `DB_PATH` | disk bilan | `/var/data/speakbot.db` (Disk mount: `/var/data`) |
| `FREE_MONTHLY_LIMIT` | yo'q | Bepul tarif: oyiga tekshiruvlar soni (standart `5`) |
| `STANDARD_MONTHLY_LIMIT` | yo'q | Standard tarif: oyiga tekshiruvlar soni (standart `30`) |
| `PREMIUM_ENABLED` | yo'q | `0` bo'lsa premium tizimi o'chiq, hamma cheklovsiz (standart `1`) |
| `WEBAPP_URL` | yo'q | Render `RENDER_EXTERNAL_URL` ni o'zi beradi |

3. **Baza saqlanishi:** SQLite fayl. Render'ning bepul rejasida fayl tizimi vaqtinchalik —
   har deploy/restartda imtihonlar va natijalar o'chadi. Doimiy saqlash uchun pullik reja + **Disk**
   (`/var/data`) va `DB_PATH=/var/data/speakbot.db` kerak.
4. Botni **faqat bitta joyda** ishga tushiring (lokal va Render bir vaqtda ishlasa `TelegramConflictError` chiqadi).
5. Deploy tugagach botga `/start` yuboring; admin sifatida «⚙️ Admin panel» ko'rinadi.

Bepul rejada servis 15 daqiqa jimlikdan keyin uxlaydi; `main.py` o'zini har 10 daqiqada ping qilib turadi.

## Lokal ishga tushirish

`speakbot/.env.example` dan `.env` yarating va `speakbot/run_local.sh` ni ishga tushiring (cloudflared kerak).

## Tariflar va admin panel

**Tariflar** (oylik tekshiruv limiti, speaking + writing birga; oy O'zbekiston vaqti bilan 1-sanada yangilanadi):

| Tarif | Standart limit | Qo'shimcha |
|---|---|---|
| 🆓 Bepul | oyiga **5** ta | — |
| ⭐ Standard | oyiga 30 ta | — |
| 💎 Pro | cheksiz | 2 mustaqil «ekspert» baholaydi (ball aniqroq) |

Limitlar admin paneldagi **⚙️ Sozlamalar** orqali o'zgartiriladi (raqam yoki «cheksiz»). Boshlang'ich qiymatlar muhit o'zgaruvchilaridan: `FREE_MONTHLY_LIMIT` (5), `STANDARD_MONTHLY_LIMIT` (30). `PREMIUM_ENABLED=0` bo'lsa hamma cheksiz.

**To'lov qo'lda:** 👥 Foydalanuvchilar → foydalanuvchini tanlang (yoki 💎 Premium → ➕ Premium berish) → tarif (Standard/Pro) → muddat (30/90/180/365 yoki istalgan kun) → to'lov izohi. Foydalanuvchiga xabar boradi; faol obunaga qayta berilsa, muddat uzayadi. Tugashiga 3 kun qolganda bot foydalanuvchiga o'zi eslatadi.

**Admin panel:**
- 📚 Testlar: yaratish, nomini o'zgartirish, nusxalash, ochish/yopish, o'chirish, «hamma testlarni o'chirish» (tasdiqlash bilan).
- 👥 Foydalanuvchilar: ro'yxat/qidiruv, karta (tarif, oylik tekshiruvlar, oxirgi natijalar, premium tarixi), premium berish/bekor qilish, 🚫 bloklash.
- 📣 Xabar yuborish: hammaga yoki tarif bo'yicha (ko'rinish + tasdiqlash, fon rejimida yuboriladi).
- 📊 Natijalar (sahifalab), 📈 Statistika (faollik, tariflar, tekshiruvlar), 👮 Adminlar (`ADMIN_IDS` — asosiy adminlar, o'chirib bo'lmaydi), ⚙️ Sozlamalar (limitlar, to'lov matni).
