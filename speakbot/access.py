"""Foydalanuvchi tekshiruv olishi mumkinmi: adminlar va premium - cheklovsiz, qolganlar - kunlik bepul limit."""
import db
from config import FREE_MONTHLY_LIMIT, PREMIUM_ENABLED, is_admin


async def check_access(user_id: int) -> tuple[bool, str]:
    """(ruxsat bormi, ruxsat yo'q bo'lsa foydalanuvchiga xabar)."""
    if not PREMIUM_ENABLED or is_admin(user_id):
        return True, ""
    if await db.get_premium_until(user_id):
        return True, ""
    used = await db.count_checks_month(user_id)
    if used >= FREE_MONTHLY_LIMIT:
        return False, (
            f"Bu oylik bepul limit tugadi ({used}/{FREE_MONTHLY_LIMIT}). "
            "Limit keyingi oy 1-sanada yangilanadi. Cheklovsiz foydalanish uchun «💎 Premium» bo'limiga o'ting."
        )
    return True, ""
