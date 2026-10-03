"""Tariflar: Bepul / Standard / Pro. Oylik tekshiruv limitlari admin paneldan (Sozlamalar) o'zgartiriladi;
o'zgartirilmagan bo'lsa - muhit o'zgaruvchilaridagi standart qiymat ishlatiladi."""
import db
from config import FREE_MONTHLY_LIMIT, RATERS, STANDARD_MONTHLY_LIMIT

PLANS = {
    "free": {"name": "Bepul", "icon": "🆓"},
    "standard": {"name": "Standard", "icon": "⭐"},
    "pro": {"name": "Pro", "icon": "💎"},
}
PAID = ("standard", "pro")
_DEFAULT_LIMIT = {"free": FREE_MONTHLY_LIMIT, "standard": STANDARD_MONTHLY_LIMIT, "pro": None}
PRO_RATERS = 2  # Pro: ikki mustaqil "ekspert" - ball barqarorroq


def name(plan: str) -> str:
    p = PLANS.get(plan, PLANS["free"])
    return f"{p['icon']} {p['name']}"


def limit_for(plan: str) -> int | None:
    """Oyiga nechta tekshiruv (None - cheksiz)."""
    raw = db.setting(f"limit_{plan}")
    if raw == "unlimited":
        return None
    if raw.isdigit():
        return int(raw)
    return _DEFAULT_LIMIT.get(plan, 0)


def limit_text(plan: str) -> str:
    lim = limit_for(plan)
    return "cheksiz" if lim is None else f"oyiga {lim} ta"


def raters_for(plan: str) -> int:
    return max(RATERS, PRO_RATERS) if plan == "pro" else RATERS
