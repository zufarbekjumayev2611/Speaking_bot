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
| `DB_PATH` | disk bilan | `/var/data/speakbot.db` (Disk mount: `/var/data`) |
| `FREE_MONTHLY_LIMIT` | yo'q | Bepul foydalanuvchi uchun oyiga tekshiruvlar soni (standart `5`) |
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

## Premium va admin panel

- O'quvchi: «💎 Premium» tugmasi — holat, oylik limit va to'lov ma'lumoti.
- Bepul foydalanuvchi oyiga `FREE_MONTHLY_LIMIT` ta tekshiruv oladi (har oyning 1-sanasida, O'zbekiston vaqti bilan yangilanadi); premium va adminlar cheklovsiz.
- To'lov **qo'lda**: admin panel → 💎 Premium → «➕ Premium berish» → ID yoki @username → muddat (30/90/180/365 yoki istalgan kun) → to'lov izohi. Foydalanuvchiga xabar boradi. Faol premiumga qayta berilsa, muddat uzayadi.
- 👮 Adminlar: `ADMIN_IDS` dagilar asosiy adminlar (o'chirib bo'lmaydi); ular panel orqali yangi admin qo'sha va olib tashlay oladi.
- 👥 Foydalanuvchilar: ro'yxatdan (yoki ID/@username qidiruvidan) foydalanuvchini tanlab, kartadan to'g'ridan-to'g'ri premium berish/bekor qilish.
- 🗑 «Hamma testlarni o'chirish» tugmasi (tasdiqlash bilan; foydalanuvchilar, premium va natijalar saqlanadi).
- 📈 Statistika va to'lov ma'lumoti matnini tahrirlash ham panelda.
