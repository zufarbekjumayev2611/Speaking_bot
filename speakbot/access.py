"""Foydalanuvchi tekshiruv olishi mumkinmi: tarif (Bepul / Standard / Pro) bo'yicha oylik limit. Adminlar - cheklovsiz."""
import db
import plans
import config
from config import PREMIUM_ENABLED, is_admin


async def get_plan(user_id: int) -> str:
    """Joriy tarif: admin -> pro, faol premium -> uning tarifi, aks holda free."""
    if is_admin(user_id):
        return "pro"
    p = await db.get_premium(user_id)
    return p["plan"] if p else "free"


async def check_access(user_id: int) -> tuple[bool, str]:
    """(ruxsat bormi, ruxsat yo'q bo'lsa foydalanuvchiga xabar)."""
    if user_id in config.BLOCKED_IDS:
        return False, "Siz botdan foydalanishdan chetlatilgansiz. Savollar bo'lsa admin bilan bog'laning."
    if not PREMIUM_ENABLED or is_admin(user_id):
        return True, ""
    plan = await get_plan(user_id)
    limit = plans.limit_for(plan)
    if limit is None:
        return True, ""
    used = await db.count_checks_month(user_id)
    if used < limit:
        return True, ""
    upsell = "«💎 Premium» bo'limida Pro tarifni ko'ring." if plan == "standard" else "«💎 Premium» bo'limida tariflarni ko'ring."
    return False, f"Bu oylik limit tugadi ({used}/{limit}). Limit keyingi oy 1-sanada yangilanadi. {upsell}"
