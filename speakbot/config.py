"""Barcha sozlamalar muhit o'zgaruvchilaridan (.env yoki Render -> Environment) olinadi.
Maxfiy kalitlarni HECH QACHON kod ichiga yozmang - repo public."""
import os
import re


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} muhit o'zgaruvchisi topilmadi.")
    return value


BOT_TOKEN = _required("BOT_TOKEN")
GROQ_API_KEY = _required("GROQ_API_KEY")  # ovozni matnga aylantirish uchun doim kerak
# Render o'zi RENDER_EXTERNAL_URL beradi (https://<nom>.onrender.com) - alohida yozish shart emas.
# Render'da doim shu servisning O'Z manzili ishlatiladi: WEBAPP_URL eski servisga qarab qolsa, mini app
# boshqa (eski) botga ulanar va keep-alive o'sha eski botni uyg'otib turardi - ikki bot bir vaqtda ishlardi.
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
WEBAPP_URL_ENV = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
WEBAPP_URL = RENDER_URL or WEBAPP_URL_ENV
WEBAPP_URL_IGNORED = bool(RENDER_URL and WEBAPP_URL_ENV and WEBAPP_URL_ENV != RENDER_URL)
if not WEBAPP_URL:
    raise RuntimeError("WEBAPP_URL muhit o'zgaruvchisi topilmadi.")

# Telegram'dan xabar olish usuli:
#   webhook - Telegram xabarni serverga o'zi yuboradi (Render Web Service'da standart: tez, ikki nusxa urishmaydi);
#   polling - bot o'zi so'rab oladi (kompyuterda ishga tushirish uchun);
#   off     - bu servis botni umuman yurgizmaydi (Render Background Worker'da standart - bot Web Service'da ishlaydi).
_default_mode = "webhook" if RENDER_URL else ("off" if os.getenv("RENDER") else "polling")
BOT_MODE = os.getenv("BOT_MODE", "").strip().lower() or _default_mode
if BOT_MODE not in ("webhook", "polling", "off"):
    raise RuntimeError("BOT_MODE faqat 'webhook', 'polling' yoki 'off' bo'lishi mumkin.")

# Adminlar: Telegram ID'lar, vergul / bo'sh joy / yangi qator bilan, masalan "111111111, 222222222"
ADMIN_IDS = [int(x) for x in re.findall(r"-?\d+", os.getenv("ADMIN_IDS", ""))]

# Imtihon tili: "tr" - turk tili (standart), "en" - ingliz tili
EXAM_LANGUAGE = os.getenv("EXAM_LANGUAGE", "tr").strip().lower()
if EXAM_LANGUAGE not in ("en", "tr"):
    raise RuntimeError("EXAM_LANGUAGE faqat 'en' yoki 'tr' bo'lishi mumkin.")

# Kim baholaydi: "groq" (bepul/arzon, standart) yoki "claude" (aniqroq, pullik)
GRADER_PROVIDER = os.getenv("GRADER_PROVIDER", "groq").strip().lower()
if GRADER_PROVIDER not in ("groq", "claude"):
    raise RuntimeError("GRADER_PROVIDER faqat 'groq' yoki 'claude' bo'lishi mumkin.")
GRADER_MODEL = os.getenv("GRADER_MODEL", "openai/gpt-oss-120b")          # Groq modeli
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")              # Claude modeli
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
if GRADER_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
    raise RuntimeError("GRADER_PROVIDER=claude uchun ANTHROPIC_API_KEY kerak.")

# Nechta mustaqil "ekspert" baholaydi (rasmiy imtihonda - 2 ta, ballar o'rtachasi olinadi).
# 2 qilinsa natija barqarorroq, lekin AI xarajati 2 baravar ko'payadi.
RATERS = max(1, min(3, int(os.getenv("RATERS", "1"))))

# Groq Whisper - nutqni matnga aylantiradi.
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo")

DB_PATH = os.getenv("DB_PATH", "speakbot.db")
# Turso (libSQL) - berilsa, ma'lumotlar lokal fayl o'rniga Turso bazasida saqlanadi.
# Render'da qaysi nom bilan kiritilgan bo'lsa ham topiladi.
def _first_env(*names: str) -> str:
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return ""


TURSO_URL = _first_env("TURSO_DATABASE_URL", "TURSO_DB_URL", "TURSO_URL", "LIBSQL_URL", "DATABASE_URL")
TURSO_TOKEN = _first_env("TURSO_AUTH_TOKEN", "TURSO_TOKEN", "TURSO_DB_TOKEN", "LIBSQL_AUTH_TOKEN", "DATABASE_TOKEN")
if TURSO_URL and not TURSO_URL.startswith(("libsql://", "https://", "http://", "wss://", "ws://")):
    TURSO_URL = ""  # masalan postgres:// - Turso emas
PORT = int(os.getenv("PORT", "8080"))


# Premium: bepul foydalanuvchilar OYIGA nechta tekshiruv (speaking + writing) olishi mumkin.
# PREMIUM_ENABLED=0 bo'lsa - hamma uchun cheklovsiz (premium tizimi o'chiriladi).
# «📊 Natijalarim» bo'limi (o'quvchi o'z natijalari ro'yxatini ko'radi). Hozircha o'chiq:
# yoqish uchun Render -> Environment'da SHOW_MY_RESULTS=1 qo'ying (kod tayyor).
SHOW_MY_RESULTS = os.getenv("SHOW_MY_RESULTS", "0").strip().lower() in ("1", "true", "yes")
PREMIUM_ENABLED = os.getenv("PREMIUM_ENABLED", "1").strip() not in ("0", "false", "no")
FREE_MONTHLY_LIMIT = max(0, int(os.getenv("FREE_MONTHLY_LIMIT", "5")))
STANDARD_MONTHLY_LIMIT = max(0, int(os.getenv("STANDARD_MONTHLY_LIMIT", "30")))  # Standard tarif: oyiga
# Pro tarif - cheksiz (admin paneldagi «Sozlamalar»dan har bir tarif limitini o'zgartirish mumkin)

# Bloklangan foydalanuvchilar (bazadan yuklanadi): bot ularga javob bermaydi.
BLOCKED_IDS: set[int] = set()

# Admin paneldan qo'shilgan adminlar (bazadan yuklanadi). ADMIN_IDS - asosiy adminlar (egalar):
# ularni panel orqali o'chirib bo'lmaydi.
EXTRA_ADMIN_IDS: set[int] = set()


def is_owner(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or user_id in EXTRA_ADMIN_IDS


def all_admin_ids() -> list[int]:
    return list(dict.fromkeys([*ADMIN_IDS, *sorted(EXTRA_ADMIN_IDS)]))
