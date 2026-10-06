"""Tariflar (Bepul / Standard / Pro) va admin paneli: foydalanuvchilar, premium, adminlar, sozlamalar, statistika.

O'quvchi:  «🚀 Tarifni yaxshilash» - tariflar, joriy tarif va oylik limit, to'lov ma'lumoti.
Admin:     👥 Foydalanuvchilar (karta -> premium berish / bloklash), 💎 Premium, 👮 Adminlar,
           ⚙️ Sozlamalar (limitlar, to'lov matni), 📈 Statistika.
To'lov QO'LDA kiritiladi: admin tarif va muddatni tanlab, to'lov izohini yozadi.
"""
import html
import logging
from datetime import datetime, timedelta

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db
import plans
from config import ADMIN_IDS, PREMIUM_ENABLED, is_admin, is_owner

log = logging.getLogger("premium")

router = Router()
router.message.filter(F.chat.type == "private")

BTN_PREMIUM = "🚀 Tarifni yaxshilash"
OLD_PREMIUM_BUTTONS = {"💎 Premium"}  # eski klaviaturalardagi tugma ham ishlashi uchun
DEFAULT_INFO = "Obuna narxi va to'lov usullari uchun admin bilan bog'laning."
DURATIONS = [30, 90, 180, 365]
INFO_KEY = "premium_info"
USERS_PER_PAGE = 8


class PremGrant(StatesGroup):
    user = State()
    plan = State()
    days = State()
    note = State()


class PremRevoke(StatesGroup):
    user = State()


class PremInfo(StatesGroup):
    text = State()


class AdminAdd(StatesGroup):
    user = State()


class UserSearch(StatesGroup):
    query = State()


class LimitEdit(StatesGroup):
    value = State()


def e(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _local(until: str) -> str:
    """UTC matnini O'zbekiston vaqtidagi sanaga (kun.oy.yil) o'giradi."""
    dt = datetime.strptime(until, "%Y-%m-%d %H:%M:%S") + timedelta(hours=5)
    return dt.strftime("%d.%m.%Y")


def _who(u: dict) -> str:
    """Foydalanuvchini ko'rsatish: ism, @username, ID."""
    name = u.get("full_name") or "Noma'lum"
    tag = f" @{u['username']}" if u.get("username") else ""
    return f"{e(name)}{tag} (<code>{u['telegram_id']}</code>)"


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _limit_line(plan: str) -> str:
    return f"{plans.name(plan)} — {plans.limit_text(plan)}"


# ======================================================================
# Bloklangan foydalanuvchilar: bot ularga javob bermaydi
# ======================================================================

class BlockMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id in config.BLOCKED_IDS and not is_admin(user.id):
            return None  # jimgina e'tiborsiz qoldiramiz
        return await handler(event, data)


# ======================================================================
# O'QUVCHI: «🚀 Tarifni yaxshilash»
# ======================================================================

@router.message(F.text.in_({BTN_PREMIUM} | OLD_PREMIUM_BUTTONS))
@router.message(Command("premium", "tarif"))
async def premium_info(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    if not PREMIUM_ENABLED:
        return await message.answer("Hozircha barcha imkoniyatlar hamma uchun cheksiz. 🎉")
    if is_admin(uid):
        return await message.answer("👮 Siz adminsiz — sizda cheklov yo'q.")

    prem = await db.get_premium(uid)
    plan = prem["plan"] if prem else "free"
    used = await db.count_checks_month(uid)
    limit = plans.limit_for(plan)
    usage = f"{used}/{limit}" if limit is not None else f"{used} (cheksiz)"
    lines = [
        "🚀 <b>Tarifni yaxshilash</b>\n",
        "<b>Tariflar:</b>",
        f"🆓 Bepul — {plans.limit_text('free')} tekshiruv",
        f"⭐ Standard — {plans.limit_text('standard')} tekshiruv",
        f"💎 Pro — {plans.limit_text('pro')} tekshiruv + <b>2 ekspert</b> baholashi (ball aniqroq)",
        "",
        f"Sizning tarifingiz: <b>{e(plans.name(plan))}</b>"
        + (f" ({_local(prem['until'])} gacha)" if prem else ""),
        f"Bu oy: <b>{usage}</b> tekshiruv",
    ]
    if not prem:
        lines += ["", e(await db.get_setting(INFO_KEY, DEFAULT_INFO))]
    await message.answer("\n".join(lines), parse_mode="HTML")


# ======================================================================
# ADMIN yordamchilari
# ======================================================================

async def _resolve(message: Message) -> dict | None:
    """Admin yuborgan ID yoki @username bo'yicha foydalanuvchini topadi; topilmasa - xabar beradi."""
    user = await db.find_user(message.text or "")
    if not user:
        await message.answer(
            "Foydalanuvchi topilmadi. Telegram ID (raqam) yoki botdan foydalangan odamning @username'ini yuboring.\n"
            "Bekor qilish: /cancel"
        )
    return user


async def _notify(bot, user_id: int, text: str):
    try:
        await bot.send_message(user_id, text, parse_mode="HTML")
    except Exception:
        log.info("Foydalanuvchiga (%s) xabar yuborib bo'lmadi - u botni ishga tushirmagan bo'lishi mumkin", user_id)


def _guard(callback: CallbackQuery) -> bool:
    return is_admin(callback.from_user.id)


# ======================================================================
# ADMIN: 💎 Premium
# ======================================================================

async def _premium_view():
    rows = await db.list_active_premium(12)
    std, pro = await db.count_active_premium("standard"), await db.count_active_premium("pro")
    soon = await db.list_expiring_premium(7, 6)
    lines = [f"💎 <b>Premium</b>\n⭐ Standard: <b>{std}</b> • 💎 Pro: <b>{pro}</b>\n"]
    if not PREMIUM_ENABLED:
        lines.append("⚠️ Premium tizimi o'chirilgan (PREMIUM_ENABLED=0) — hamma uchun cheksiz.\n")
    for r in rows:
        lines.append(f"• {plans.PLANS.get(r['plan'], plans.PLANS['pro'])['icon']} {_who(r)} — {_local(r['until'])} gacha")
    total = std + pro
    if total > len(rows):
        lines.append(f"… va yana {total - len(rows)} ta")
    if soon:
        lines.append("\n⏰ <b>7 kun ichida tugaydi:</b>")
        lines += [f"• {_who(r)} — {_local(r['until'])}" for r in soon]
    kb = _kb([
        [_btn("➕ Premium berish", "pg_start"), _btn("➖ Bekor qilish", "pr_start")],
        [_btn("⬅️ Orqaga", "admin")],
    ])
    return "\n".join(lines)[:4000], kb


@router.callback_query(F.data == "adm_prem")
async def premium_menu(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    text, kb = await _premium_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ---- Premium berish: foydalanuvchi -> tarif -> muddat -> to'lov izohi ----

@router.callback_query(F.data == "pg_start")
async def grant_start(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    await state.set_state(PremGrant.user)
    await callback.message.answer(
        "➕ <b>Premium berish</b>\n\nFoydalanuvchining <b>Telegram ID</b> raqamini yoki <b>@username</b>'ini yuboring.\n"
        "(@username faqat botdan foydalangan odamlar uchun ishlaydi. Ro'yxatdan tanlash: «👥 Foydalanuvchilar».) "
        "Bekor qilish: /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


async def _ask_plan(target: Message, state: FSMContext, user: dict):
    await state.clear()
    await state.update_data(target=user["telegram_id"], label=_who(user))
    await state.set_state(PremGrant.plan)
    current = await db.get_premium(user["telegram_id"])
    now = f"\nHozir: <b>{e(plans.name(current['plan']))}</b>, {_local(current['until'])} gacha" if current else ""
    await target.answer(
        f"👤 {_who(user)}{now}\n\nQaysi tarif?\n"
        f"⭐ Standard — {plans.limit_text('standard')}\n💎 Pro — {plans.limit_text('pro')}",
        parse_mode="HTML",
        reply_markup=_kb([[_btn("⭐ Standard", "pgp:standard"), _btn("💎 Pro", "pgp:pro")]]),
    )


@router.message(PremGrant.user)
async def grant_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    user = await _resolve(message)
    if user:
        await _ask_plan(message, state, user)


@router.callback_query(F.data.startswith("pgu:"))
async def user_grant(callback: CallbackQuery, state: FSMContext):
    """Foydalanuvchi kartasidan premium berish."""
    if not _guard(callback):
        return await callback.answer()
    user = await db.find_user(callback.data.split(":")[1])
    await callback.answer()
    await _ask_plan(callback.message, state, user)


@router.callback_query(PremGrant.plan, F.data.startswith("pgp:"))
async def grant_plan(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    plan = callback.data.split(":")[1]
    if plan not in plans.PAID:
        return await callback.answer()
    await state.update_data(plan=plan)
    await state.set_state(PremGrant.days)
    kb = _kb([[_btn(f"{d} kun", f"pgd:{d}") for d in DURATIONS[:2]], [_btn(f"{d} kun", f"pgd:{d}") for d in DURATIONS[2:]]])
    await callback.message.answer(
        f"{plans.name(plan)}. Necha kunlik beramiz? Tugmani bosing yoki kunlar sonini raqam bilan yozing.", reply_markup=kb
    )
    await callback.answer()


async def _ask_note(target: Message, state: FSMContext, days: int):
    await state.update_data(days=days)
    await state.set_state(PremGrant.note)
    await target.answer(
        f"🗓 {days} kun.\n\nTo'lov haqida izoh yozing (summa, to'lov usuli — masalan: <i>50 000 so'm, Click</i>). "
        "Izoh kerak bo'lmasa «-» yuboring.",
        parse_mode="HTML",
    )


@router.callback_query(PremGrant.days, F.data.startswith("pgd:"))
async def grant_days_button(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await callback.answer()
    await _ask_note(callback.message, state, int(callback.data.split(":")[1]))


@router.message(PremGrant.days)
async def grant_days_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text.isdigit() or not 1 <= int(text) <= 3650:
        return await message.answer("Kunlar sonini 1 dan 3650 gacha raqam bilan yozing yoki tugmani bosing.")
    await _ask_note(message, state, int(text))


@router.message(PremGrant.note)
async def grant_note(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    note = (message.text or "").strip()
    if not note:
        return await message.answer("Izohni matn bilan yozing yoki «-» yuboring.")
    data = await state.get_data()
    await state.clear()
    target, days, plan = data["target"], data["days"], data.get("plan", "pro")
    until = await db.grant_premium(target, days, "" if note == "-" else note[:200], message.from_user.id, plan)
    await _notify(
        message.bot, target,
        f"{e(plans.name(plan))} <b>faollashtirildi!</b>\nTugash sanasi: <b>{_local(until)}</b>\n"
        f"Limit: {plans.limit_text(plan)} tekshiruv.\n\nOmad! 🎉",
    )
    text, kb = await _premium_view()
    await message.answer(
        f"✅ {data['label']} — {e(plans.name(plan))} <b>{_local(until)}</b> gacha ({days} kun qo'shildi).",
        parse_mode="HTML",
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ---- Premiumni bekor qilish ----

@router.callback_query(F.data == "pr_start")
async def revoke_start(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    await state.set_state(PremRevoke.user)
    await callback.message.answer(
        "➖ <b>Premiumni bekor qilish</b>\n\nFoydalanuvchining Telegram ID'si yoki @username'ini yuboring. Bekor qilish: /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


async def _confirm_revoke(target: Message, user: dict, back: str):
    prem = await db.get_premium(user["telegram_id"])
    if not prem:
        return await target.answer(f"👤 {_who(user)} da faol premium yo'q.", parse_mode="HTML")
    await target.answer(
        f"👤 {_who(user)}\n{e(plans.name(prem['plan']))}: {_local(prem['until'])} gacha.\n\nBekor qilamizmi?",
        parse_mode="HTML",
        reply_markup=_kb([[_btn("✅ Ha, bekor qilish", f"prv:{user['telegram_id']}"), _btn("❌ Yo'q", back)]]),
    )


@router.message(PremRevoke.user)
async def revoke_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    user = await _resolve(message)
    if not user:
        return
    await state.clear()
    await _confirm_revoke(message, user, "adm_prem")


@router.callback_query(F.data.startswith("usr_rv:"))
async def user_revoke_ask(callback: CallbackQuery):
    if not _guard(callback):
        return await callback.answer()
    uid = int(callback.data.split(":")[1])
    await callback.answer()
    await _confirm_revoke(callback.message, await db.find_user(str(uid)), f"usr:{uid}")


@router.callback_query(F.data.startswith("prv:"))
async def revoke_do(callback: CallbackQuery):
    if not _guard(callback):
        return await callback.answer()
    await db.revoke_premium(int(callback.data.split(":")[1]), callback.from_user.id)
    text, kb = await _premium_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Premium bekor qilindi")


# ======================================================================
# ADMIN: ⚙️ Sozlamalar (limitlar va to'lov matni)
# ======================================================================

async def _settings_view():
    info = await db.get_setting(INFO_KEY, DEFAULT_INFO)
    text = (
        "⚙️ <b>Sozlamalar</b>\n\n<b>Oylik tekshiruv limitlari:</b>\n"
        + "\n".join(f"• {e(_limit_line(p))}" for p in plans.PLANS)
        + f"\n\n<b>To'lov ma'lumoti</b> (o'quvchi «🚀 Tarifni yaxshilash» bo'limida ko'radi):\n<i>{e(info)}</i>"
    )
    kb = _kb([
        [_btn("✏️ Bepul", "lim:free"), _btn("✏️ Standard", "lim:standard"), _btn("✏️ Pro", "lim:pro")],
        [_btn("📝 To'lov ma'lumoti matni", "pi_edit")],
        [_btn("⬅️ Orqaga", "admin")],
    ])
    return text, kb


@router.callback_query(F.data == "adm_settings")
async def settings_menu(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    text, kb = await _settings_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("lim:"))
async def limit_edit(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    plan = callback.data.split(":")[1]
    if plan not in plans.PLANS:
        return await callback.answer()
    await state.clear()
    await state.set_state(LimitEdit.value)
    await state.update_data(plan=plan)
    await callback.message.answer(
        f"{e(plans.name(plan))}: hozir <b>{e(plans.limit_text(plan))}</b>.\n\n"
        "Oyiga necha ta tekshiruv bo'lsin? Raqam yozing (masalan <code>10</code>) yoki cheksiz uchun "
        "<code>cheksiz</code> deb yozing. Bekor qilish: /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(LimitEdit.value)
async def limit_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip().lower()
    if text in ("cheksiz", "unlimited", "-"):
        value = "unlimited"
    elif text.isdigit() and 0 <= int(text) <= 100000:
        value = str(int(text))
    else:
        return await message.answer("Raqam (0 dan 100000 gacha) yoki «cheksiz» yozing.")
    plan = (await state.get_data())["plan"]
    await db.set_setting(f"limit_{plan}", value)
    await state.clear()
    view, kb = await _settings_view()
    await message.answer("✅ Saqlandi.")
    await message.answer(view, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "pi_edit")
async def info_edit(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    await state.set_state(PremInfo.text)
    current = await db.get_setting(INFO_KEY, DEFAULT_INFO)
    await callback.message.answer(
        "📝 <b>To'lov ma'lumoti</b> — o'quvchi «🚀 Tarifni yaxshilash» tugmasini bosganda shu matnni ko'radi "
        "(narxlar, karta raqami, admin bilan aloqa va h.k.).\n\n"
        f"Hozirgi matn:\n<i>{e(current)}</i>\n\nYangi matnni yuboring (ko'pi bilan 1000 belgi). "
        "Standart matnga qaytarish uchun «-» yuboring. Bekor qilish: /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(PremInfo.text)
async def info_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").strip()
    if not text:
        return await message.answer("Matn yuboring yoki «-» yozing.")
    if len(text) > 1000:
        return await message.answer(f"Matn juda uzun ({len(text)} belgi). 1000 belgidan oshmasin.")
    await db.set_setting(INFO_KEY, "" if text == "-" else text)
    await state.clear()
    view, kb = await _settings_view()
    await message.answer("✅ Saqlandi.")
    await message.answer(view, parse_mode="HTML", reply_markup=kb)


# ======================================================================
# ADMIN: 👮 Adminlar
# ======================================================================

async def _admins_view(viewer_id: int):
    lines = ["👮 <b>Adminlar</b>\n", "<b>Asosiy adminlar</b> (o'chirib bo'lmaydi):"]
    for uid in ADMIN_IDS:
        u = await db.get_user(uid) or {"telegram_id": uid}
        lines.append(f"• {_who(u)}")
    extra = await db.list_admins()
    lines.append("\n<b>Qo'shilgan adminlar:</b>" if extra else "\nQo'shilgan adminlar yo'q.")
    rows = []
    for a in extra:
        lines.append(f"• {_who(a)}")
        if is_owner(viewer_id):
            label = (a.get("full_name") or str(a["telegram_id"]))[:30]
            rows.append([_btn(f"🗑 {label}", f"adr:{a['telegram_id']}")])
    if is_owner(viewer_id):
        rows.append([_btn("➕ Admin qo'shish", "ad_add")])
    else:
        lines.append("\n<i>Adminlarni faqat asosiy adminlar o'zgartira oladi.</i>")
    rows.append([_btn("⬅️ Orqaga", "admin")])
    return "\n".join(lines), _kb(rows)


@router.callback_query(F.data == "adm_admins")
async def admins_menu(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    text, kb = await _admins_view(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "ad_add")
async def admin_add_start(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        return await callback.answer("Faqat asosiy adminlar admin qo'sha oladi.", show_alert=True)
    await state.clear()
    await state.set_state(AdminAdd.user)
    await callback.message.answer(
        "➕ <b>Admin qo'shish</b>\n\nYangi adminning Telegram ID'si yoki @username'ini yuboring "
        "(u avval botga /start yuborgan bo'lishi kerak). Bekor qilish: /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminAdd.user)
async def admin_add_save(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    user = await _resolve(message)
    if not user:
        return
    uid = user["telegram_id"]
    await state.clear()
    if is_admin(uid):
        return await message.answer(f"👤 {_who(user)} allaqachon admin.", parse_mode="HTML")
    await db.add_admin(uid, message.from_user.id)
    await _notify(message.bot, uid, "👮 Siz admin etib tayinlandingiz. Menyuni yangilash uchun /start yuboring.")
    text, kb = await _admins_view(message.from_user.id)
    await message.answer(f"✅ {_who(user)} admin qilindi.", parse_mode="HTML")
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adr:"))
async def admin_remove_ask(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return await callback.answer("Faqat asosiy adminlar.", show_alert=True)
    uid = int(callback.data.split(":")[1])
    u = await db.get_user(uid) or {"telegram_id": uid}
    await callback.message.edit_text(
        f"👤 {_who(u)}\n\nAdminlikdan olamizmi?",
        parse_mode="HTML",
        reply_markup=_kb([[_btn("✅ Ha, olish", f"adrd:{uid}"), _btn("❌ Yo'q", "adm_admins")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adrd:"))
async def admin_remove_do(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        return await callback.answer("Faqat asosiy adminlar.", show_alert=True)
    uid = int(callback.data.split(":")[1])
    if is_owner(uid):  # asosiy adminlarni o'chirib bo'lmaydi
        return await callback.answer("Asosiy adminni o'chirib bo'lmaydi.", show_alert=True)
    await db.remove_admin(uid)
    await _notify(callback.bot, uid, "Siz endi admin emassiz. Menyuni yangilash uchun /start yuboring.")
    text, kb = await _admins_view(callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Olib tashlandi")


# ======================================================================
# ADMIN: 👥 Foydalanuvchilar (ro'yxat -> karta -> premium / bloklash)
# ======================================================================

async def _users_view(page: int):
    total = await db.count_users()
    pages = max(1, -(-total // USERS_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    users = await db.list_users(page * USERS_PER_PAGE, USERS_PER_PAGE)
    lines = [f"👥 <b>Foydalanuvchilar</b> — jami {total} ta (sahifa {page + 1}/{pages})\n"
             "Foydalanuvchini tanlang yoki ID/@username bo'yicha qidiring."]
    rows = []
    for u in users:
        mark = "🚫 " if u["blocked"] else (plans.PLANS[u["plan"] or "pro"]["icon"] + " " if u["is_premium"] else "")
        label = (u.get("full_name") or u.get("username") or str(u["telegram_id"]))[:28]
        rows.append([_btn(f"{mark}{label}", f"usr:{u['telegram_id']}")])
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"adm_users:{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"adm_users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_btn("🔎 Qidirish", "usr_search")])
    rows.append([_btn("⬅️ Orqaga", "admin")])
    return "\n".join(lines), _kb(rows)


@router.callback_query(F.data.startswith("adm_users:"))
async def users_menu(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    text, kb = await _users_view(int(callback.data.split(":")[1]))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


async def _user_card(user: dict):
    uid = user["telegram_id"]
    prem = await db.get_premium(uid)
    plan = "pro" if is_admin(uid) else (prem["plan"] if prem else "free")
    used = await db.count_checks_month(uid)
    limit = plans.limit_for(plan)
    status = f"{e(plans.name(plan))}" + (f" — <b>{_local(prem['until'])}</b> gacha" if prem else "")
    if is_admin(uid):
        status += " • 👮 admin"
    if uid in config.BLOCKED_IDS:
        status += " • 🚫 bloklangan"
    lines = [
        f"👤 {_who(user)}",
        status,
        f"Bu oy tekshiruvlar: <b>{used}</b>" + (f" / {limit}" if limit is not None else " (cheksiz)"),
    ]
    results = await db.user_results(uid, 5)
    if results:
        lines.append("\n<b>Oxirgi natijalar:</b>")
        for r in results:
            icon = "✍️" if r["kind"] == "writing" else "🎙"
            score = f"{float(r['raw_score']):g}" if r.get("raw_score") is not None else str(r["score"])
            lines.append(f"{icon} {e(r['title'] or '?')} — {score} ({e(r['level'])})")
    hist = await db.premium_history(uid, 3)
    if hist:
        lines.append("\n<b>Premium tarixi:</b>")
        for h in hist:
            if h["action"] == "grant":
                note = f", {e(h['amount'])}" if h.get("amount") else ""
                lines.append(f"+ {e(plans.name(h['plan'] or 'pro'))} {h['days']} kun{note}")
            else:
                lines.append("− bekor qilingan")
    rows = [[_btn("💎 Premium berish / uzaytirish", f"pgu:{uid}")]]
    if prem:
        rows.append([_btn("➖ Premiumni bekor qilish", f"usr_rv:{uid}")])
    if not is_owner(uid):
        rows.append([_btn("✅ Blokdan chiqarish" if uid in config.BLOCKED_IDS else "🚫 Bloklash", f"usr_bl:{uid}")])
    rows.append([_btn("⬅️ Ro'yxat", "adm_users:0")])
    return "\n".join(lines)[:4000], _kb(rows)


@router.callback_query(F.data.startswith("usr:"))
async def user_open(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    text, kb = await _user_card(await db.find_user(callback.data.split(":")[1]))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("usr_bl:"))
async def user_block_toggle(callback: CallbackQuery):
    if not _guard(callback):
        return await callback.answer()
    uid = int(callback.data.split(":")[1])
    if is_owner(uid):
        return await callback.answer("Asosiy adminni bloklab bo'lmaydi.", show_alert=True)
    blocked = uid not in config.BLOCKED_IDS
    await db.set_blocked(uid, blocked)
    text, kb = await _user_card(await db.find_user(str(uid)))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Bloklandi" if blocked else "Blokdan chiqarildi")


@router.callback_query(F.data == "usr_search")
async def user_search_start(callback: CallbackQuery, state: FSMContext):
    if not _guard(callback):
        return await callback.answer()
    await state.clear()
    await state.set_state(UserSearch.query)
    await callback.message.answer("🔎 Foydalanuvchining Telegram ID'si yoki @username'ini yuboring. Bekor qilish: /cancel")
    await callback.answer()


@router.message(UserSearch.query)
async def user_search(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    user = await _resolve(message)
    if not user:
        return
    await state.clear()
    text, kb = await _user_card(user)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ======================================================================
# ADMIN: 📈 Statistika
# ======================================================================

@router.callback_query(F.data == "adm_stats")
async def stats_view(callback: CallbackQuery):
    if not _guard(callback):
        return await callback.answer()
    s = await db.get_stats()
    text = (
        "📈 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{s['users']}</b> (bugun +{s['new_today']}, 7 kunda +{s['new_week']})\n"
        f"🔥 Oxirgi 7 kunda faol: <b>{s['active_week']}</b>\n"
        f"🚫 Bloklangan: <b>{s['blocked']}</b>\n\n"
        f"⭐ Standard: <b>{s['standard']}</b> • 💎 Pro: <b>{s['pro']}</b>\n"
        f"🧾 30 kunda berilgan obunalar: <b>{s['grants_month']}</b>\n\n"
        f"🎙 Speaking: <b>{s['speaking']}</b> • ✍️ Writing: <b>{s['writing']}</b>\n"
        f"📅 Tekshiruvlar — bugun: <b>{s['today']}</b>, 7 kun: <b>{s['week']}</b>, 30 kun: <b>{s['month']}</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_kb([[_btn("⬅️ Orqaga", "admin")]]))
    await callback.answer()
