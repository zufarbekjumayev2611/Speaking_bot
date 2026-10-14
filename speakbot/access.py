"""Foydalanuvchi tekshiruv olishi mumkinmi: tarif bo'yicha Konuşma va Yazma limiti ALOHIDA.
Bepul - kalendar oy; pullik - obunaning joriy 30 kunlik davri. Adminlar - cheklovsiz."""
from datetime import datetime, timedelta, timezone

import config
import db
import plans
from config import PREMIUM_ENABLED, is_admin

_FMT = "%Y-%m-%d %H:%M:%S"
# Hozir AI tekshirayotgan ishlar ((foydalanuvchi, bo'lim) bo'yicha): ular hali "done" emas, lekin limitga
# kiradi - aks holda ikki ishni bir vaqtda topshirib, limitdan oshib ketish mumkin edi.
_pending: dict[tuple[int, str], int] = {}


async def get_plan(user_id: int) -> str:
    """Joriy tarif: admin -> vip, faol obuna -> uning tarifi, aks holda free."""
    if is_admin(user_id):
        return "vip"
    p = await db.get_premium(user_id)
    return p["plan"] if p else "free"


def period_bounds(until: str | None) -> tuple[str | None, str | None]:
    """Pullik obunaning joriy 30 kunlik davri: (boshlanishi, keyingi davr boshlanishi) UTC matnda.
    Obuna `until` da tugaydi; davrlar undan orqaga 30 kundan sanaladi. Bepulda (None, None)."""
    if not until:
        return None, None
    end = datetime.strptime(until, _FMT).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    step = timedelta(days=plans.PERIOD_DAYS)
    start = end - step
    while start > now:  # obuna bir necha davrga berilgan - hozirgi davrni topamiz
        start -= step
    nxt = start + step
    return start.strftime(_FMT), (nxt.strftime(_FMT) if nxt < end else None)


async def usage(user_id: int, kind: str) -> dict:
    """{plan, used, limit (None - cheksiz), next_reset (UTC matn yoki None), until}."""
    prem = None if is_admin(user_id) else await db.get_premium(user_id)
    plan = "vip" if is_admin(user_id) else (prem["plan"] if prem else "free")
    start, nxt = period_bounds(prem["until"] if prem else None)
    used = await db.count_checks(user_id, kind, start)
    return {"plan": plan, "used": used, "limit": plans.limit_for(plan, kind), "next_reset": nxt,
            "until": prem["until"] if prem else None}


def _local_date(stamp: str) -> str:
    return (datetime.strptime(stamp, _FMT) + timedelta(hours=5)).strftime("%d.%m.%Y")


async def check_access(user_id: int, kind: str, _extra: int = 0) -> tuple[bool, str]:
    """(ruxsat bormi, ruxsat yo'q bo'lsa foydalanuvchiga xabar). kind: speaking | writing."""
    if is_admin(user_id):
        return True, ""
    if user_id in config.BLOCKED_IDS:
        return False, "Siz botdan foydalanishdan chetlatilgansiz. Savollar bo'lsa admin bilan bog'laning."
    if not PREMIUM_ENABLED:
        return True, ""
    u = await usage(user_id, kind)
    if u["limit"] is None:
        return True, ""
    used = u["used"] + _pending.get((user_id, kind), 0) - _extra
    if used < u["limit"]:
        return True, ""
    label = plans.KIND_LABEL.get(kind, kind)
    if u["plan"] == "free":
        when = "Limit keyingi oy 1-sanada yangilanadi."
    elif u["next_reset"]:
        when = f"Yangi 30 kunlik davr {_local_date(u['next_reset'])} da boshlanadi."
    else:
        when = f"Obunangiz {_local_date(u['until'])} gacha."
    upsell = " Ko'proq kerak bo'lsa — «🚀 Tarifni yaxshilash»." if u["plan"] != "vip" else ""
    return False, f"{label} limiti tugadi ({used}/{u['limit']}). {when}{upsell}"


async def acquire_check(user_id: int, kind: str) -> tuple[bool, str]:
    """Tekshiruvni boshlashdan oldin: joy band qilinadi (await'dan OLDIN), keyin limit tekshiriladi.
    Ruxsat berilsa, tekshiruv tugagach release_check() chaqirilishi shart."""
    key = (user_id, kind)
    _pending[key] = _pending.get(key, 0) + 1
    try:
        allowed, reason = await check_access(user_id, kind, _extra=1)
    except BaseException:
        release_check(user_id, kind)
        raise
    if not allowed:
        release_check(user_id, kind)
    return allowed, reason


def release_check(user_id: int, kind: str):
    key = (user_id, kind)
    n = _pending.get(key, 0) - 1
    if n > 0:
        _pending[key] = n
    else:
        _pending.pop(key, None)


async def usage_text(user_id: int) -> str:
    """«🎙 Konuşma: 3/30 • ✍️ Yazma: 1/30» + qachon yangilanishi (profil, tarif sahifasi, admin kartasi uchun)."""
    parts, info = [], None
    for kind, icon in (("speaking", "🎙"), ("writing", "✍️")):
        info = await usage(user_id, kind)
        lim = "cheksiz" if info["limit"] is None else str(info["limit"])
        parts.append(f"{icon} {plans.KIND_LABEL[kind]}: <b>{info['used']}/{lim}</b>")
    text = " • ".join(parts)
    if info["plan"] == "free":
        return f"Bu oy: {text}"
    if info["next_reset"]:
        return f"Shu 30 kunda: {text}\n(yangi davr {_local_date(info['next_reset'])} da)"
    return f"Shu davrda: {text}"
