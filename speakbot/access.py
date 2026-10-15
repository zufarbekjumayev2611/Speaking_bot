"""Foydalanuvchi tekshiruv olishi mumkinmi: tarif bo'yicha Konuşma va Yazma limiti ALOHIDA, MOCK bilan o'lchanadi.
Mockni birinchi marta ishlaganda limitdan 1 ta olinadi; shu davrda uning qismlari, full mock va qayta
ishlashlar bepul. Bepul - kalendar oy; pullik - obunaning joriy 30 kunlik davri. Adminlar - cheklovsiz."""
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


def is_own(exam: dict | None, user_id: int) -> bool:
    """O'quvchi o'zi yaratgan test: uni cheksiz topshiradi (limit test yaratilganda bir marta olingan)."""
    return bool(exam and exam.get("owner_id") == user_id)


async def mock_opened(user_id: int, kind: str, exam: dict | None) -> bool:
    """Test mockning qismi va bu mock shu davrda allaqachon ochilgan (limit olingan) bo'lsa - True."""
    if not exam or not exam.get("mock_id"):
        return False
    prem = await db.get_premium(user_id)
    start, _ = period_bounds(prem["until"] if prem else None)
    return await db.mock_used(user_id, kind, exam["mock_id"], start)


async def check_access(user_id: int, kind: str, _extra: int = 0, exam: dict | None = None) -> tuple[bool, str]:
    """(ruxsat bormi, ruxsat yo'q bo'lsa foydalanuvchiga xabar). kind: speaking | writing.
    exam - shu davrda ochilgan mockning qismi (yoki o'quvchining eski shaxsiy testi) bo'lsa, limit olinmaydi."""
    if is_admin(user_id):
        return True, ""
    if user_id in config.BLOCKED_IDS:
        return False, "Siz botdan foydalanishdan chetlatilgansiz. Savollar bo'lsa admin bilan bog'laning."
    if not PREMIUM_ENABLED or is_own(exam, user_id):
        return True, ""
    if _extra == 0 and await mock_opened(user_id, kind, exam):
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
    return False, f"{label} limiti tugadi ({used}/{u['limit']} mock). {when}{upsell}"


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


async def reserve(user_id: int, kind: str, exam: dict | None) -> tuple[bool, str, bool]:
    """Baholashdan oldin: (ruxsat, sabab, joy band qilindimi). Shu davrda ochilgan mock (yoki o'z testi) -
    limit olinmaydi (band qilinmaydi). Band qilingan bo'lsa, baholashdan keyin release_check() shart."""
    if is_admin(user_id) or is_own(exam, user_id) or (PREMIUM_ENABLED and await mock_opened(user_id, kind, exam)):
        if user_id in config.BLOCKED_IDS:
            return False, "Siz botdan foydalanishdan chetlatilgansiz.", False
        return True, "", False
    allowed, reason = await acquire_check(user_id, kind)
    return allowed, reason, allowed


def release_check(user_id: int, kind: str):
    key = (user_id, kind)
    n = _pending.get(key, 0) - 1
    if n > 0:
        _pending[key] = n
    else:
        _pending.pop(key, None)


async def usage_text(user_id: int) -> str:
    """«🎙 Konuşma: 3/30 mock • ✍️ Yazma: 1/30 mock» + qachon yangilanishi (profil, tarif sahifasi, admin kartasi uchun)."""
    parts, info = [], None
    for kind, icon in (("speaking", "🎙"), ("writing", "✍️")):
        info = await usage(user_id, kind)
        lim = "cheksiz" if info["limit"] is None else str(info["limit"])
        parts.append(f"{icon} {plans.KIND_LABEL[kind]}: <b>{info['used']}/{lim}</b> mock")
    text = " • ".join(parts)
    if info["plan"] == "free":
        return f"Bu oy: {text}"
    if info["next_reset"]:
        return f"Shu 30 kunda: {text}\n(yangi davr {_local_date(info['next_reset'])} da)"
    return f"Shu davrda: {text}"


async def usage_block(user_id: int) -> str:
    """Profil va tarif sahifasi uchun chiroyli blok: har bo'lim bo'yicha chiziq va qachon yangilanishi."""
    lines, info = [], None
    for kind, icon in (("speaking", "🎙"), ("writing", "✍️")):
        info = await usage(user_id, kind)
        lim = "∞" if info["limit"] is None else str(info["limit"])
        lines.append(f"{icon} {plans.KIND_LABEL[kind]}  {plans.bar(info['used'], info['limit'])}  <b>{info['used']}/{lim}</b> mock")
    if info["plan"] == "free":
        lines.append("🔄 Limit har oyning 1-sanasida yangilanadi")
    elif info["next_reset"]:
        lines.append(f"🔄 Yangi 30 kunlik davr: {_local_date(info['next_reset'])}")
    return "\n".join(lines)
