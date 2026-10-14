"""Tariflar: Bepul / Standard / Pro / VIP.

Limit Konuşma va Yazma uchun ALOHIDA hisoblanadi:
  🆓 Bepul    — oyiga 2 Konuşma + 2 Yazma (kalendar oy, O'zbekiston vaqti)
  ⭐ Standard — 30 + 30, 30 000 so'm / 30 kun
  💎 Pro      — 60 + 60, 50 000 so'm / 30 kun, 2 ekspert baholashi
  👑 VIP      — 100 + 100, 70 000 so'm / 30 kun, 2 ekspert baholashi
Pullik tarifda limit har 30 kunlik davrda yangilanadi (obuna 60 kunga berilsa - ikki davr).
Limit va narxlarni admin «⚙️ Sozlamalar» dan o'zgartira oladi (bazada saqlanadi)."""
import db
from config import RATERS

KINDS = ("speaking", "writing")
KIND_LABEL = {"speaking": "Konuşma", "writing": "Yazma"}
PERIOD_DAYS = 30

PLANS = {
    "free": {"name": "Bepul", "icon": "🆓", "price": 0, "limits": {"speaking": 2, "writing": 2}, "raters": 1},
    "standard": {"name": "Standard", "icon": "⭐", "price": 30000, "limits": {"speaking": 30, "writing": 30}, "raters": 1},
    "pro": {"name": "Pro", "icon": "💎", "price": 50000, "limits": {"speaking": 60, "writing": 60}, "raters": 2},
    "vip": {"name": "VIP", "icon": "👑", "price": 70000, "limits": {"speaking": 100, "writing": 100}, "raters": 2},
}
PAID = ("standard", "pro", "vip")


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
    """«30 Konuşma + 30 Yazma» (bepulda - «oyiga ...»)."""
    s, w = limit_for(plan, "speaking"), limit_for(plan, "writing")
    text = f"{_count(s)} Konuşma + {_count(w)} Yazma"
    return f"oyiga {text}" if plan == "free" else text


def offer_line(plan: str) -> str:
    """Tarif qatori: «⭐ Standard — 30 Konuşma + 30 Yazma — 30 000 so'm / 30 kun»."""
    line = f"{name(plan)} — {limits_text(plan)}"
    if plan != "free":
        line += f" — <b>{money(price_for(plan))}</b> / {PERIOD_DAYS} kun"
    if raters_for(plan) > 1:
        line += " • 2 ekspert baholashi"
    return line


def raters_for(plan: str) -> int:
    return max(RATERS, _info(plan)["raters"])
