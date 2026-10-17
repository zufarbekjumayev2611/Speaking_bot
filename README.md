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
| `CLAUDE_MODEL` | yo'q | `claude-haiku-5-5` (standart, eng arzon) |
| `WRITING_GRADER` | yo'q | `claude` (standart: Yazmani Claude baholaydi + xatolarni qayta tekshiradi; kalit bo'lmasa Groq) yoki `groq` |
| `GRADER_CONCURRENCY` | yo'q | Bir vaqtda nechta AI so'rovi (standart `2`; Groq pullik rejasida oshirish mumkin) |

AI band bo'lsa (429 «navbat band»), bot o'zi 4 → 10 → 20 soniya kutib qayta urinadi; baribir band bo'lsa va
ikkinchi xizmat kaliti bor bo'lsa (Groq ↔ Claude), avtomatik o'shanga o'tadi.
| `TURSO_DATABASE_URL` | tavsiya | `libsql://<baza>.turso.io` — berilsa, ma'lumotlar Turso'da saqlanadi (deploy/restartda o'chmaydi, disk kerak emas) |
| `TURSO_AUTH_TOKEN` | Turso bilan | Turso token (`turso db tokens create <baza>`) |
| `DB_PATH` | disk bilan | `/var/data/speakbot.db` (Disk mount: `/var/data`) |
| `PREMIUM_ENABLED` | yo'q | `0` bo'lsa premium tizimi o'chiq, hamma cheklovsiz (standart `1`) |
| `WEBAPP_URL` | yo'q | Render `RENDER_EXTERNAL_URL` ni o'zi beradi |
| `USERS_API_KEY` | yo'q | Berilsa (kamida 16 belgi) `GET /api/users` ishlaydi — pastga qarang |

3. **Baza saqlanishi:** SQLite fayl. Render'ning bepul rejasida fayl tizimi vaqtinchalik —
   har deploy/restartda imtihonlar va natijalar o'chadi. Doimiy saqlash uchun pullik reja + **Disk**
   (`/var/data`) va `DB_PATH=/var/data/speakbot.db` kerak.
4. Botni **faqat bitta joyda** ishga tushiring (lokal va Render bir vaqtda ishlasa `TelegramConflictError` chiqadi).
5. Deploy tugagach botga `/start` yuboring; admin sifatida «⚙️ Admin panel» ko'rinadi.

Bepul rejada servis 15 daqiqa jimlikdan keyin uxlaydi; `main.py` o'zini har 10 daqiqada ping qilib turadi.

## Foydalanuvchilar API (`GET /api/users`)

Sherik yoki boshqa tizim Turso tokenisiz faqat foydalanuvchilar ro'yxatini olishi uchun.
`USERS_API_KEY` qo'yilmasa endpoint o'chiq (404).

```
curl -H "Authorization: Bearer $USERS_API_KEY" "https://<servis>.onrender.com/api/users?plan=standard&limit=100"
```

Parametrlar: `plan` (`free` / `standard` / `pro`), `since` (`YYYY-MM-DD` — shu kundan keyin qo'shilganlar),
`limit` (standart 500, ko'pi bilan 1000), `offset`. Javob:
`{"total", "limit", "offset", "users": [{"telegram_id", "name", "username", "registered_at", "plan",
"plan_until", "speaking_count", "writing_count", "last_activity", "blocked"}]}` (vaqtlar UTC, ISO 8601).
Kalitni almashtirish uchun Render'da `USERS_API_KEY` ni o'zgartirib, qayta deploy qiling.

## 👑 Boss va adminlar

- **/boss** (yoki Admin panel → «👑 Boss paneli») — hamma narsa bir joyda: bugungi holat, kutilayotgan cheklar,
  30 kunlik tushum, o'quvchilar, jamoa va barcha bo'limlar (Mocklar, Foydalanuvchilar, Premium, Natijalar, Xabar,
  Statistika, Adminlarni boshqarish, Tariflar/narx/karta, Tizim holati, Boss sayti). Adminlarni Boss saytidan ham
  qo'shish, bo'limlarini yoqish/yopish va olib tashlash mumkin.

- **🖥 Boss sayti** (`/boss`) — Admin panel yoki Boss paneldagi tugma Telegram ichida to'liq ekranli sahifani ochadi:
  tushum (7 / 30 / 90 kun / 1 yil), kim qaysi tarifni sotib oldi va kim tasdiqladi, to'lov cheklari, o'quvchilar
  (qidiruv, tarif bo'yicha), kunlik tekshiruvlar, jamoa va huquqlar, tariflar. Faqat `ADMIN_IDS` dagi bosslar kiradi
  (Telegram imzosi tekshiriladi); sayt faqat ko'rsatadi, o'zgartirishlar botda.
- **Admin bilan bog'lanish:** o'quvchi «👤 Profil» ostidagi tugma, `/yordam` yoki tarif sahifasi orqali adminga yozadi.
  Username'ni Boss → ⚙️ Tariflar va narxlar → «👤 Admin username» dan o'zgartiradi.

- **Boss** — Render'dagi `ADMIN_IDS`: hamma huquq + «👑 Boss paneli» (30 kunlik tushum va kim nechta obuna bergani,
  adminlar va ularning huquqlari, tarif/narx sozlamalari, tizim holati).
- **Admin** — botda qo'shilganlar. Boss har bir admin uchun bo'limlarni yoqadi/yopadi: Testlar, Foydalanuvchilar,
  Premium, Natijalar, Xabar yuborish, Statistika. Yopiq bo'lim tugmalari adminga ko'rinmaydi, eski tugma bosilsa
  ham markaziy filtr (`roles.py`) to'xtatadi.
- Mocklar va foydalanuvchilar ro'yxati 10 tadan chiqadi (qolgani — sahifalab yoki 🔎 qidiruv); o'quvchining
  mocklar ro'yxati ham 10 tadan, katalog xotirada keshlanadi (yuzlab mock bo'lsa ham yengil).

## 📚 Mocklar

O'quvchi menyusida **«🎙 Konuşma»** va **«✍️ Yazma»** tugmalari: bo'lim → mock (10 tadan) → Full mock yoki alohida qism.
- **Konuşma:** 🔥 *Full mock* — Part 1.1 → 1.2 → 2 → 3 ketma-ket (natija: qismlar yig'indisi /21 va taxminiy
  standart ball /75), yoki istalgan qismni alohida (Part 1.1, 1.2, 2, 3).
- **Yazma:** 🔥 *Full mock* (1 va 2-qism, 60 daqiqa, /75), yoki faqat 1-qism (2 xat) / 2-qism (blog).

Admin: «📚 Mocklar» → «➕ Yangi mock» (raqami avtomatik, ichida 5 ta bo'sh qism) → qismni bosib savol /
topshiriq qo'shadi. Tayyor qismlar darhol ko'rinadi; Full mock 4 ta Konuşma qismi tayyor bo'lganda chiqadi.
Birinchi ishga tushishda 5 ta namunaviy full mock yaratiladi (Part 1.2 va 2 rasmsiz — rasmni admin qo'shadi).
O'quvchilar endi o'zi test yaratmaydi; mockka kirmagan eski testlar «🗂 Eski testlar» arxivida.

## Lokal ishga tushirish

`speakbot/.env.example` dan `.env` yarating va `speakbot/run_local.sh` ni ishga tushiring (cloudflared kerak).

## Tariflar va admin panel

**Tariflar** — Konuşma va Yazma limiti **alohida** hisoblanadi:

| Tarif | Konuşma | Yazma | Narx | Qo'shimcha |
|---|---|---|---|---|
| 🆓 Bepul | oyiga 2 | oyiga 2 | — | — |
| ⭐ Standard | 30 | 30 | 30 000 so'm / 30 kun | — |
| 💎 Pro | 60 | 60 | 50 000 so'm / 30 kun | 2 ekspert baholashi |
| 👑 VIP | 100 | 100 | 70 000 so'm / 30 kun | 2 ekspert baholashi |

Limit **mock** bilan o'lchanadi: mockning Konuşma (yoki Yazma) qismini birinchi marta ishlash — 1 ta; shu davrda
uning qolgan qismlari bepul. Har qism bir marta ishlanadi: ishlangan qism ✅ bilan yopiladi, biror qismi
ishlangan bo'limda Full mock chiqmaydi (adminlarga cheklov yo'q). Bepul limit kalendar oyda (O'zbekiston vaqti, 1-sanada) yangilanadi; pullik limit — obunaning har 30 kunlik
davrida (60 kunga berilsa — ikki davr). Limit va narxlar admin paneldagi **⚙️ Sozlamalar** orqali o'zgartiriladi.
`PREMIUM_ENABLED=0` bo'lsa hamma cheksiz.

**To'lovni tasdiqlash (chek):** o'quvchi «🚀 Tarifni yaxshilash» da karta raqamini ko'radi, pul o'tkazadi,
«✅ To'lovni tasdiqlash» (yoki `/tolov`) → tarifni tanlaydi → chek rasmini yuboradi. Chek «💎 Premium» huquqi bor
adminlarga keladi; admin summaga mos tarif tugmasini (Standard / Pro / VIP) bosadi — obuna 30 kunga avtomatik
beriladi va o'quvchiga xabar boradi («❌ Rad etish» ham bor). Karta raqami: Boss → ⚙️ Sozlamalar → «💳 Karta raqami»
(yoki Render'da `CARD_NUMBER`). Admin username: `ADMIN_CONTACT` (standart `@muhammad_hocam`).

**To'lov qo'lda:** 👥 Foydalanuvchilar → foydalanuvchini tanlang (yoki 💎 Premium → ➕ Premium berish) → tarif (Standard/Pro/VIP) → muddat (30/90/180/365 yoki istalgan kun) → to'lov izohi. Foydalanuvchiga xabar boradi; faol obunaga qayta berilsa, muddat uzayadi. Tugashiga 3 kun qolganda bot foydalanuvchiga o'zi eslatadi.

**Admin panel:**
- 📚 Mocklar: yaratish, qismlarini to'ldirish va tahrirlash, nomini o'zgartirish, o'chirish (tasdiqlash bilan).
- 👥 Foydalanuvchilar: ro'yxat/qidiruv, karta (tarif, oylik tekshiruvlar, oxirgi natijalar, premium tarixi), premium berish/bekor qilish, 🚫 bloklash.
- 📣 Xabar yuborish: hammaga yoki tarif bo'yicha (ko'rinish + tasdiqlash, fon rejimida yuboriladi).
- 📊 Natijalar (sahifalab), 📈 Statistika (faollik, tariflar, tekshiruvlar), 👮 Adminlar (`ADMIN_IDS` — asosiy adminlar, o'chirib bo'lmaydi), ⚙️ Sozlamalar (limitlar, to'lov matni).


## Tezlik

Turso'ga har so'rov tarmoq orqali (~0.1–0.3 s). Shuning uchun: o'quvchining tarifi va yakunlangan ishlari xotirada
keshlanadi (yangi ish tugaganda / obuna berilganda yangilanadi), mustaqil so'rovlar parallel yuboriladi, FSM holati
xotirada turadi, webhook xabarlari fonda ishlanadi (`handle_in_background`). Ko'p tugmalar bazaga umuman bormaydi.
Eng katta tezlik uchun Turso bazasi va Render servisi **bir xil mintaqada** bo'lsin (Boss → 🩺 Tizim holati →
«Baza javob tezligi»: 100 ms dan past bo'lishi yaxshi).

## ⚠️ Xatolar soni: 50 tagacha (admin talabi bilan)

Natijada har bir qism / matn uchun **50 tagacha** xato ko'rsatiladi (avval 10 edi; `grader.py` → `MAX_MISTAKES`).
Bu admin talabi bilan qilingan. Ma'lum xavflar:

- **Noto'g'ri «xato»lar ko'payishi mumkin** — ro'yxat qancha uzun bo'lsa, AI to'g'ri so'zni ham xato deb ko'rsatishi
  shuncha ko'p bo'ladi (masalan «saatine» holati). Ikkinchi tekshiruv va turkcha istisnolar tekshiruvi ko'pini olib
  tashlaydi, lekin 100 % emas.
- **AI xarajati oshadi** — xatosi ko'p (kuchsiz) o'quvchida bitta tekshiruv taxminan 1,5–2,5 baravar qimmatlashadi
  (Yazma ~70 so'm → ~150–200 so'm). Xatosi kam o'quvchida deyarli o'zgarmaydi.
- **Tekshiruv sekinlashadi** — ko'p xatoli matnda natija 10–30 soniya kechroq keladi; bir vaqtda ko'p odam
  yuborsa navbat ham sekinroq yuradi.
- **Natija xabari uzun** — 50 ta xatoli natija Telegram'da 2–3 xabarga bo'linib keladi (bot o'zi bo'ladi).
- **O'quvchiga og'ir** — juda uzun ro'yxat o'quvchini charchatadi; xatolar muhimidan boshlab chiqadi.
- **Konuşma'da** — nutq ovozdan matnga aylantiriladi; aylantirishdagi noaniqliklar ham «xato» bo'lib ko'rinishi
  mumkin, ro'yxat uzun bo'lganda bu ko'proq seziladi.

Javob kesilib qolmasligi uchun AI javob chegaralari oshirilgan (Groq 12 000, Claude 24 000 token, Claude kutish
vaqti 180 soniya). Muammo bo'lsa — `MAX_MISTAKES` ni yana 10–25 ga qaytarish kifoya.
