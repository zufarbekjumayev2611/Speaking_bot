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
| `WEBAPP_URL` | yo'q | Render `RENDER_EXTERNAL_URL` ni o'zi beradi |

3. **Baza saqlanishi:** SQLite fayl. Render'ning bepul rejasida fayl tizimi vaqtinchalik —
   har deploy/restartda imtihonlar va natijalar o'chadi. Doimiy saqlash uchun pullik reja + **Disk**
   (`/var/data`) va `DB_PATH=/var/data/speakbot.db` kerak.
4. Botni **faqat bitta joyda** ishga tushiring (lokal va Render bir vaqtda ishlasa `TelegramConflictError` chiqadi).
5. Deploy tugagach botga `/start` yuboring; admin sifatida «⚙️ Admin panel» ko'rinadi.

Bepul rejada servis 15 daqiqa jimlikdan keyin uxlaydi; `main.py` o'zini har 10 daqiqada ping qilib turadi.

## Lokal ishga tushirish

`speakbot/.env.example` dan `.env` yarating va `speakbot/run_local.sh` ni ishga tushiring (cloudflared kerak).
