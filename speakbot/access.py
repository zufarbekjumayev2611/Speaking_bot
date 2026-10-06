"""Foydalanuvchi tekshiruv olishi mumkinmi: tarif (Bepul / Standard / Pro) bo'yicha oylik limit. Adminlar - cheklovsiz."""
import db
import plans
import config
from config import PREMIUM_ENABLED, is_admin

# Hozir AI tekshirayotgan ishlar (foydalanuvchi bo'yicha): ular hali "done" emas, lekin limitga kiradi -
# aks holda ikki ishni bir vaqtda topshirib, limitdan oshib ketish mumkin edi.
_pending: dict[int, int] = {}


async def get_plan(user_id: int) -> str:
    """Joriy tarif: admin -> pro, faol premium -> uning tarifi, aks holda free."""
    if is_admin(user_id):
        return "pro"
    p = await db.get_premium(user_id)
    return p["plan"] if p else "free"


async def check_access(user_id: int, _extra: int = 0) -> tuple[bool, str]:
    """(ruxsat bormi, ruxsat yo'q bo'lsa foydalanuvchiga xabar)."""
    if is_admin(user_id):
        return True, ""
    if user_id in config.BLOCKED_IDS:
        return False, "Siz botdan foydalanishdan chetlatilgansiz. Savollar bo'lsa admin bilan bog'laning."
    if not PREMIUM_ENABLED:
        return True, ""
    plan = await get_plan(user_id)
    limit = plans.limit_for(plan)
    if limit is None:
        return True, ""
    used = await db.count_checks_month(user_id) + _pending.get(user_id, 0) - _extra
    if used < limit:
        return True, ""
    upsell = ("«🚀 Tarifni yaxshilash» bo'limida Pro tarifni ko'ring." if plan == "standard"
              else "«🚀 Tarifni yaxshilash» bo'limida tariflarni ko'ring.")
    return False, f"Bu oylik limit tugadi ({used}/{limit}). Limit keyingi oy 1-sanada yangilanadi. {upsell}"


async def acquire_check(user_id: int) -> tuple[bool, str]:
    """Tekshiruvni boshlashdan oldin: joy band qilinadi (await'dan OLDIN), keyin limit tekshiriladi.
    Ruxsat berilsa, tekshiruv tugagach release_check() chaqirilishi shart."""
    _pending[user_id] = _pending.get(user_id, 0) + 1
    allowed, reason = await check_access(user_id, _extra=1)
    if not allowed:
        release_check(user_id)
    return allowed, reason


def release_check(user_id: int):
    n = _pending.get(user_id, 0) - 1
    if n > 0:
        _pending[user_id] = n
    else:
        _pending.pop(user_id, None)
