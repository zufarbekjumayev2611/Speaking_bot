"""Foydalanuvchi tekshiruv olishi mumkinmi: tarif bo'yicha Konuşma va Yazma limiti ALOHIDA, MOCK bilan o'lchanadi.
Mockni birinchi marta ishlaganda limitdan 1 ta olinadi; shu davrda uning qismlari, full mock va qayta
ishlashlar bepul. Bepul - kalendar oy; pullik - obunaning joriy 30 kunlik davri. Adminlar - cheklovsiz."""
import asyncio
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


def period_bounds(prem: dict | None) -> tuple[str | None, str | None]:
    """Pullik obunaning joriy 30 kunlik davri: (boshlanishi, keyingi davr boshlanishi) UTC matnda.
    Davrlar obuna boshlangan vaqtdan (period_start) 30 kundan sanaladi; eski yozuvlarda - tugash sanasidan
    orqaga. Bepulda (None, None)."""
    if not prem or not prem.get("until"):
        return None, None
    end = datetime.strptime(prem["until"], _FMT)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    step = timedelta(days=plans.PERIOD_DAYS)
    start = datetime.strptime(prem.get("period_start") or db.legacy_period_start(prem["until"]), _FMT)
    while start + step <= now:  # obuna bir necha davrga berilgan - hozirgi davrni topamiz
        start += step
    nxt = start + step
    return start.strftime(_FMT), (nxt.strftime(_FMT) if nxt < end else None)


async def limit_reached(user_id: int, kind: str | None = None) -> bool:
    """Pullik obunachining joriy 30 kunlik limiti tugaganmi (kind berilmasa - Konuşma YOKI Yazma).
    Tugagan bo'lsa, yangi to'lov obunani hozirdan qayta boshlaydi (db.grant_premium restart=True)."""
    if not PREMIUM_ENABLED or is_admin(user_id) or not await db.get_premium(user_id):
        return False
    for k in ([kind] if kind else plans.KINDS):
        u = await usage(user_id, k)
        if u["limit"] is not None and u["used"] >= u["limit"]:
            return True
    return False


def _in_period(stamp: str | None, start: str | None, now_month: str) -> bool:
    """Ish shu limit davriga kiradimi: pullik - `start` dan keyin, bepul - shu kalendar oyda (UTC+5)."""
    if not stamp:
        return False
    if start:
        return stamp >= start
    try:
        return (datetime.strptime(stamp[:19], _FMT) + timedelta(hours=5)).strftime("%Y-%m") == now_month
    except ValueError:
        return False


def _period_rows(rows: list[dict], start: str | None) -> list[dict]:
    now_month = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m")
    return [r for r in rows if _in_period(r.get("created_at"), start, now_month)]


def count_used(rows: list[dict], kind: str, start: str | None) -> int:
    """Sarflangan limit (db.count_checks bilan bir xil qoida): davrda ochilgan har mock 1 ta, mockka kirmagan
    eski testlarda har tekshiruv 1 ta, o'zi yaratgan (eski) shaxsiy test - yaratilganda 1 ta."""
    mocks, n = set(), 0
    for r in _period_rows(rows, start):
        if r["kind"] == kind and not r.get("own"):
            if r.get("mock_id"):
                mocks.add(r["mock_id"])
            else:
                n += 1
        elif r["kind"] == "own_" + kind:
            n += 1
    return n + len(mocks)


async def usage(user_id: int, kind: str) -> dict:
    """{plan, used, limit (None - cheksiz), next_reset (UTC matn yoki None), until}. Bazaga ko'pi bilan 1 so'rov
    (o'quvchining yakunlangan ishlari va tarifi xotirada keshlanadi)."""
    if is_admin(user_id):
        prem, rows = None, await db.user_done(user_id)
    else:  # tarif va ishlar parallel olinadi (keshda bo'lmasa ham - bitta kutish)
        prem, rows = await asyncio.gather(db.get_premium(user_id), db.user_done(user_id))
    plan = "vip" if is_admin(user_id) else (prem["plan"] if prem else "free")
    start, nxt = period_bounds(prem)
    used = count_used(rows, kind, start)
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
    start, _ = period_bounds(prem)
    mock_id = exam["mock_id"]
    return any(r["kind"] == kind and r.get("mock_id") == mock_id for r in _period_rows(await db.user_done(user_id), start))


# ---------------------------------------------------------------- 🎁 sinov mock (bepul tarif)
# Bepul foydalanuvchiga faqat bitta sinov mock ko'rinadi va ochiladi (har qismi bir marta); qolgan mocklar -
# faqat obunachilar uchun. Obunachiga esa tarifi bo'yicha ro'yxatdagi birinchi N ta mock ochiq
# (⭐ Standard - 30, 💎 Pro - 60; plans.mocks_for), qolganlari unga ham ko'rinmaydi.

TRIAL_ONLY_MSG = ("🔒 Bu mock faqat obunachilar uchun (⭐ Standard, 💎 Pro). Bepul tarifda — faqat 🎁 sinov "
                  "mock. Tarifni «🚀 Tarifni yaxshilash» bo'limidan tanlang.")


def plan_locked_msg(plan: str) -> str:
    return (f"🔒 Bu mock tarifingizga kirmaydi: {plans.name(plan)} — birinchi {plans.mocks_for(plan)} ta mock. "
            "Ko'proq mock — «🚀 Tarifni yaxshilash».")


async def free_trial_only(user_id: int) -> bool:
    """Bepul foydalanuvchi (premium tizimi yoqiq, admin emas, obunasi yo'q) - faqat sinov mock."""
    return PREMIUM_ENABLED and not is_admin(user_id) and await get_plan(user_id) == "free"


async def visible_mocks(user_id: int) -> set[int] | None:
    """Foydalanuvchiga ochiq mocklar (None - hammasi): bepulda - sinov mock, obunachida - tarifi bo'yicha
    ro'yxatdagi birinchi N ta mock, adminlarga va premium tizimi o'chiq bo'lsa - hammasi."""
    if not PREMIUM_ENABLED or is_admin(user_id):
        return None
    plan = await get_plan(user_id)
    if plan == "free":
        trial = await db.trial_mock_id()
        return {trial} if trial else set()
    n = plans.mocks_for(plan)
    return None if n is None else set((await db.mock_order())[:n])


async def lock_reason(user_id: int, exam: dict | None) -> str | None:
    """Mock foydalanuvchiga yopiq bo'lsa - sababi (o'quvchiga ko'rsatiladi), ochiq bo'lsa None."""
    if not exam or not exam.get("mock_id"):
        return None
    visible = await visible_mocks(user_id)
    if visible is None or exam["mock_id"] in visible:
        return None
    plan = await get_plan(user_id)
    return TRIAL_ONLY_MSG if plan == "free" else plan_locked_msg(plan)


async def mock_locked(user_id: int, exam: dict | None) -> bool:
    return await lock_reason(user_id, exam) is not None


async def trial_status(user_id: int) -> str:
    """Bepul tarif bloki (profil, tarif sahifasi, mocklar ro'yxati uchun)."""
    trial = await db.trial_mock_id()
    if trial and trial in await mocks_done(user_id):
        return "🎁 Sinov mock: <b>boshlangan ✅</b> — har qism bir marta; qolgan mocklar uchun tarif tanlang"
    return "🎁 Sinov mock: <b>ochiq</b> — bir marta bepul ishlaysiz"


async def check_access(user_id: int, kind: str, _extra: int = 0, exam: dict | None = None) -> tuple[bool, str]:
    """(ruxsat bormi, ruxsat yo'q bo'lsa foydalanuvchiga xabar). kind: speaking | writing.
    exam - shu davrda ochilgan mockning qismi (yoki o'quvchining eski shaxsiy testi) bo'lsa, limit olinmaydi."""
    if is_admin(user_id):
        return True, ""
    if user_id in config.BLOCKED_IDS:
        return False, "Siz botdan foydalanishdan chetlatilgansiz. Savollar bo'lsa admin bilan bog'laning."
    locked = await lock_reason(user_id, exam)
    if locked:
        return False, locked
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
    if u["plan"] == "free":
        upsell = f" Ko'proq kerak bo'lsa — «🚀 Tarifni yaxshilash» yoki admin: {db.admin_contact()}."
    else:  # pullik: kutmasdan qayta sotib olsa - yangi 30 kun va to'liq limit hoziroq
        upsell = " Kutmasdan: «🔄 Yangi 30 kun» — limit hoziroq yangilanadi."
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
    locked = await lock_reason(user_id, exam)  # obuna imtihon davomida tugagan bo'lishi mumkin
    if locked:
        return False, locked, False
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
        return await trial_status(user_id)
    if info["next_reset"]:
        return f"Shu 30 kunda: {text}\n(yangi davr {_local_date(info['next_reset'])} da)"
    return f"Shu davrda: {text}"


async def usage_block(user_id: int) -> str:
    """Profil va tarif sahifasi uchun chiroyli blok: har bo'lim bo'yicha chiziq va qachon yangilanishi."""
    if await free_trial_only(user_id):
        return await trial_status(user_id) + "\n🔓 Qolgan mocklar — ⭐ Standard va 💎 Pro obunachilari uchun"
    lines, info = [], None
    for kind, icon in (("speaking", "🎙"), ("writing", "✍️")):
        info = await usage(user_id, kind)
        lim = "∞" if info["limit"] is None else str(info["limit"])
        lines.append(f"{icon} {plans.KIND_LABEL[kind]}  {plans.bar(info['used'], info['limit'])}  <b>{info['used']}/{lim}</b> mock")
    if info["plan"] == "free":
        lines.append("🔄 Bepul limit har oyning 1-sanasida yangilanadi")
    elif info["next_reset"]:
        lines.append(f"🔄 Yangi 30 kunlik davr: {_local_date(info['next_reset'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------- mockni qayta ishlab bo'lmaydi

REDO_MSG = "Bu qismni allaqachon ishlagansiz — mockni qayta ishlab bo'lmaydi. Keyingi mockni tanlang 📚"


async def done_parts(user_id: int, mock_id: int | None) -> dict[str, set[str]]:
    """Mockdagi yopilgan (ishlangan) qismlar: {"speaking": {"1.1", ..., "full"?}, "writing": {"1", "2", "full"?}}.
    Har qism bir marta ishlanadi; biror qismi ishlangan bo'lsa, o'sha bo'limning Full mock'i yopiladi.
    Adminlar uchun hech narsa yopilmaydi."""
    out: dict[str, set[str]] = {"speaking": set(), "writing": set()}
    if not mock_id or is_admin(user_id):
        return out
    for r in await db.user_done(user_id):
        if r.get("mock_id") != mock_id or r["kind"] not in out:
            continue
        kind, part = r["kind"], r.get("part")
        if kind == "writing":
            out["writing"] |= {"1", "2"} if part == "full" else {part}
        else:
            out["speaking"].add(part)
    for kind in out:
        if out[kind]:
            out[kind].add("full")
    return out


async def already_done(user_id: int, exam: dict | None, part: str | None = None) -> bool:
    """Shu qism (yazmada - rejim: full / 1 / 2) avval ishlanganmi. part berilmasa - testning o'z qismi."""
    if not exam or not exam.get("mock_id"):
        return False
    kind = exam.get("kind") or "speaking"
    part = part or exam.get("part")
    done = (await done_parts(user_id, exam["mock_id"]))[kind]
    if kind == "writing" and part == "full":
        return bool(done)
    return part in done


async def mocks_done(user_id: int, kind: str | None = None) -> set[int]:
    """O'quvchi kamida bir qismini ishlagan mocklar (✅ belgisi) - keshdan, bazaga qo'shimcha so'rovsiz."""
    return {r["mock_id"] for r in await db.user_done(user_id)
            if r.get("mock_id") and (r["kind"] == kind if kind else r["kind"] in ("speaking", "writing"))}


async def done_counts(user_id: int) -> dict:
    """{"speaking": n, "writing": m} - jami yakunlangan ishlar (profil uchun)."""
    out = {"speaking": 0, "writing": 0}
    for r in await db.user_done(user_id):
        if r["kind"] in out:
            out[r["kind"]] += 1
    return out
