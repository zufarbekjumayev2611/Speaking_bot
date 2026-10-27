"""Tariflar: Bepul / Standard / Pro (VIP hozircha sotuvda emas - faqat avval olganlar uchun ishlaydi).

Limit Konuşma va Yazma uchun ALOHIDA, MOCK bilan hisoblanadi (mockni ochish - 1 ta; uning qismlari va
qolgan qismlari shu davrda bepul; har qism bir marta ishlanadi):
  🆓 Bepul    — faqat 🎁 1 ta sinov mock (bir marta); qolgan mocklar ko'rinmaydi
  ⭐ Standard — 30 ta mock (30 + 30), 59 000 so'm / 30 kun; ro'yxatda birinchi 30 ta mock ko'rinadi
  💎 Pro      — 60 ta mock (60 + 60), 109 000 so'm / 30 kun, 2 ekspert baholashi; birinchi 60 ta mock
  👑 VIP      — 100 + 100 (sotuvda emas)
Pullik tarifda limit har 30 kunlik davrda yangilanadi (obuna 60 kunga berilsa - ikki davr).
Limit va narxlarni admin «⚙️ Sozlamalar» dan o'zgartira oladi (bazada saqlanadi)."""
import db
from config import PREMIUM_ENABLED, RATERS

KINDS = ("speaking", "writing")
KIND_LABEL = {"speaking": "Konuşma", "writing": "Yazma"}
PERIOD_DAYS = 30

PLANS = {
    "free": {"name": "Bepul", "icon": "🆓", "price": 0, "limits": {"speaking": 2, "writing": 2}, "raters": 1},
    "standard": {"name": "Standard", "icon": "⭐", "price": 59000, "limits": {"speaking": 30, "writing": 30}, "raters": 1},
    "pro": {"name": "Pro", "icon": "💎", "price": 109000, "limits": {"speaking": 60, "writing": 60}, "raters": 2},
    "vip": {"name": "VIP", "icon": "👑", "price": 70000, "limits": {"speaking": 100, "writing": 100}, "raters": 2},
}
PAID = ("standard", "pro", "vip")  # pullik tariflar (statistika, mavjud obunalar)
ON_SALE = ("standard", "pro")       # o'quvchiga sotiladigan tariflar (VIP hozircha chiqarilmaydi)

# Bir martalik yangilash: yangi narx va limitlar bazadagi eski sozlamalar ustidan bir marta yoziladi
# (keyin admin «⚙️ Sozlamalar» dan bemalol o'zgartiradi - qayta ishga tushganda qaytib ketmaydi).
_UPDATE_KEY = "plans_update_2026_10"
_UPDATE = {"price_standard": "59000", "price_pro": "109000",
           "limit_standard_speaking": "30", "limit_standard_writing": "30",
           "limit_pro_speaking": "60", "limit_pro_writing": "60"}


async def apply_update():
    if db.setting(_UPDATE_KEY):
        return
    for key, value in _UPDATE.items():
        await db.set_setting(key, value)
    await db.set_setting(_UPDATE_KEY, "1")


def on_sale(current: str | None = None) -> list[str]:
    """Sotuvdagi tariflar; o'quvchining hozirgi tarifi sotuvda bo'lmasa ham (masalan VIP) - ro'yxatda qoladi."""
    return [p for p in PAID if p in ON_SALE or p == current]


def _info(plan: str) -> dict:
    return PLANS.get(plan, PLANS["free"])


def name(plan: str) -> str:
    p = _info(plan)
    return f"{p['icon']} {p['name']}"


def limit_for(plan: str, kind: str) -> int | None:
    """Davr uchun nechta tekshiruv (None - cheksiz). Admin sozlamasi bo'lsa - o'sha."""
    raw = db.setting(f"limit_{plan}_{kind}")
    if raw == "unlimited":
        return None
    if raw.isdigit():
        return int(raw)
    return _info(plan)["limits"].get(kind, 0)


def price_for(plan: str) -> int:
    raw = db.setting(f"price_{plan}")
    return int(raw) if raw.isdigit() else _info(plan)["price"]


def money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " so'm"


def _count(n: int | None) -> str:
    return "cheksiz" if n is None else str(n)


def limits_text(plan: str) -> str:
    """«30 ta mock (Konuşma + Yazma)» (bepulda - faqat sinov mock, access.free_trial_only)."""
    if plan == "free" and PREMIUM_ENABLED:
        return "🎁 1 ta sinov mock (bir marta)"
    s, w = limit_for(plan, "speaking"), limit_for(plan, "writing")
    if s == w:
        return f"{_count(s)} ta mock (Konuşma + Yazma)"
    return f"{_count(s)} Konuşma + {_count(w)} Yazma mock"


def mocks_for(plan: str) -> int | None:
    """Tarifda ro'yxatda nechta mock ko'rinadi (birinchi N ta; None - hammasi): limit bilan bir xil -
    Standard 30, Pro 60."""
    s, w = limit_for(plan, "speaking"), limit_for(plan, "writing")
    return None if s is None or w is None else max(s, w)


def offer_line(plan: str) -> str:
    """Tarif qatori: «⭐ Standard — 30 Konuşma + 30 Yazma — 30 000 so'm / 30 kun»."""
    line = f"{name(plan)} — {limits_text(plan)}"
    if plan != "free":
        line += f" — <b>{money(price_for(plan))}</b> / {PERIOD_DAYS} kun"
    if raters_for(plan) > 1:
        line += " • 2 ekspert baholashi"
    return line


def bar(used: int, limit: int | None, width: int = 10) -> str:
    """Sarf chizig'i: ▰▰▰▱▱▱▱▱▱▱ (cheksiz - to'liq bo'sh chiziq o'rniga ∞)."""
    if limit is None:
        return "∞"
    if limit <= 0:
        return "▱" * width
    full = min(width, round(width * used / limit))
    return "▰" * full + "▱" * (width - full)


def per_mock(plan: str) -> int | None:
    """1 ta mock (Konuşma + Yazma) narxi (so'm) - tariflarni solishtirish uchun."""
    total = mocks_for(plan) or 0
    price = price_for(plan)
    return round(price / total) if price and total else None


def raters_for(plan: str) -> int:
    return max(RATERS, _info(plan)["raters"])
