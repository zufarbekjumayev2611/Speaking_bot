"""Premium obuna va kengaytirilgan admin paneli.

O'quvchi:  «💎 Premium» - holati, oylik limit, to'lov ma'lumoti.
Admin:     💎 Premium (berish / bekor qilish / to'lov matni), 👮 Adminlar (qo'shish / o'chirish),
           📈 Statistika. To'lov QO'LDA kiritiladi: admin foydalanuvchiga necha kunlik premium berishni tanlaydi.
"""
import html
import logging
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from config import ADMIN_IDS, FREE_MONTHLY_LIMIT, PREMIUM_ENABLED, is_admin, is_owner

log = logging.getLogger("premium")

router = Router()
router.message.filter(F.chat.type == "private")

BTN_PREMIUM = "💎 Premium"
DEFAULT_INFO = "Premium narxi va to'lov usullari uchun admin bilan bog'laning."
DURATIONS = [30, 90, 180, 365]
INFO_KEY = "premium_info"


class PremGrant(StatesGroup):
    user = State()
    days = State()
    note = State()


class PremRevoke(StatesGroup):
    user = State()


class PremInfo(StatesGroup):
    text = State()


class AdminAdd(StatesGroup):
    user = State()


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


# ======================================================================
# O'QUVCHI: «💎 Premium»
# ======================================================================

@router.message(F.text == BTN_PREMIUM)
@router.message(Command("premium"))
async def premium_info(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    if not PREMIUM_ENABLED:
        return await message.answer("Hozircha barcha imkoniyatlar hamma uchun cheklovsiz. 🎉")
    if is_admin(uid):
        return await message.answer("👮 Siz adminsiz — sizda cheklov yo'q.")
    until = await db.get_premium_until(uid)
    if until:
        return await message.answer(
            f"💎 <b>Premium faol</b>\nTugash sanasi: <b>{_local(until)}</b>\n\nTekshiruvlar soni cheklanmagan.",
            parse_mode="HTML",
        )
    used = await db.count_checks_month(uid)
    info = await db.get_setting(INFO_KEY, DEFAULT_INFO)
    await message.answer(
        f"🆓 <b>Bepul reja</b>\nBu oy: <b>{used}/{FREE_MONTHLY_LIMIT}</b> tekshiruv (limit har oyning 1-sanasida yangilanadi).\n\n"
        f"💎 <b>Premium</b> — tekshiruvlar soni cheklanmagan.\n\n{e(info)}",
        parse_mode="HTML",
    )


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


# ======================================================================
# ADMIN: 💎 Premium
# ======================================================================

async def _premium_view():
    rows = await db.list_active_premium(15)
    total = await db.count_active_premium()
    lines = [f"💎 <b>Premium</b>\nFaol obunalar: <b>{total}</b> • bepul limit: <b>{FREE_MONTHLY_LIMIT}</b>/oy\n"]
    if not PREMIUM_ENABLED:
        lines.append("⚠️ Premium tizimi o'chirilgan (PREMIUM_ENABLED=0) — hamma uchun cheklovsiz.\n")
    for r in rows:
        lines.append(f"• {_who(r)} — {_local(r['until'])} gacha")
    if total > len(rows):
        lines.append(f"… va yana {total - len(rows)} ta")
    kb = _kb([
        [_btn("➕ Premium berish", "pg_start"), _btn("➖ Bekor qilish", "pr_start")],
        [_btn("📝 To'lov ma'lumoti matni", "pi_edit")],
        [_btn("⬅️ Orqaga", "admin")],
    ])
    return "\n".join(lines), kb


@router.callback_query(F.data == "adm_prem")
async def premium_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    text, kb = await _premium_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ---- Premium berish: foydalanuvchi -> muddat -> to'lov izohi ----

@router.callback_query(F.data == "pg_start")
async def grant_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await state.set_state(PremGrant.user)
    await callback.message.answer(
        "➕ <b>Premium berish</b>\n\nFoydalanuvchining <b>Telegram ID</b> raqamini yoki <b>@username</b>'ini yuboring.\n"
        "(@username faqat botdan foydalangan odamlar uchun ishlaydi.) Bekor qilish: /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


async def _ask_days(target: Message, state: FSMContext, user: dict):
    await state.clear()
    await state.update_data(target=user["telegram_id"], label=_who(user))
    await state.set_state(PremGrant.days)
    kb = _kb([[_btn(f"{d} kun", f"pgd:{d}") for d in DURATIONS[:2]], [_btn(f"{d} kun", f"pgd:{d}") for d in DURATIONS[2:]]])
    await target.answer(
        f"👤 {_who(user)}\n\nNecha kunlik premium beramiz? Tugmani bosing yoki kunlar sonini raqam bilan yozing.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(PremGrant.user)
async def grant_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    user = await _resolve(message)
    if not user:
        return
    await _ask_days(message, state, user)


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
    if not is_admin(callback.from_user.id):
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
    target, days = data["target"], data["days"]
    until = await db.grant_premium(target, days, "" if note == "-" else note[:200], message.from_user.id)
    await _notify(
        message.bot, target,
        f"💎 <b>Premium faollashtirildi!</b>\nTugash sanasi: <b>{_local(until)}</b>\n\nEndi tekshiruvlar soni cheklanmagan. Omad! 🎉",
    )
    text, kb = await _premium_view()
    await message.answer(f"✅ {data['label']} — premium <b>{_local(until)}</b> gacha ({days} kun qo'shildi).",
                         parse_mode="HTML")
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ---- Premiumni bekor qilish ----

@router.callback_query(F.data == "pr_start")
async def revoke_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await state.set_state(PremRevoke.user)
    await callback.message.answer(
        "➖ <b>Premiumni bekor qilish</b>\n\nFoydalanuvchining Telegram ID'si yoki @username'ini yuboring. Bekor qilish: /cancel",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(PremRevoke.user)
async def revoke_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    user = await _resolve(message)
    if not user:
        return
    await state.clear()
    until = await db.get_premium_until(user["telegram_id"])
    if not until:
        return await message.answer(f"👤 {_who(user)} da faol premium yo'q.", parse_mode="HTML")
    await message.answer(
        f"👤 {_who(user)}\nPremium: {_local(until)} gacha.\n\nBekor qilamizmi?",
        parse_mode="HTML",
        reply_markup=_kb([[_btn("✅ Ha, bekor qilish", f"prv:{user['telegram_id']}"), _btn("❌ Yo'q", "adm_prem")]]),
    )


@router.callback_query(F.data.startswith("prv:"))
async def revoke_do(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await db.revoke_premium(int(callback.data.split(":")[1]), callback.from_user.id)
    text, kb = await _premium_view()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("Premium bekor qilindi")


# ---- To'lov ma'lumoti matni ----

@router.callback_query(F.data == "pi_edit")
async def info_edit(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await state.set_state(PremInfo.text)
    current = await db.get_setting(INFO_KEY, DEFAULT_INFO)
    await callback.message.answer(
        "📝 <b>To'lov ma'lumoti</b> — o'quvchi «💎 Premium» tugmasini bosganda shu matnni ko'radi "
        "(narx, karta raqami, admin bilan aloqa va h.k.).\n\n"
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
    view, kb = await _premium_view()
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
    if not is_admin(callback.from_user.id):
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
# ADMIN: 👥 Foydalanuvchilar (ro'yxat -> karta -> premium berish)
# ======================================================================

USERS_PER_PAGE = 8


class UserSearch(StatesGroup):
    query = State()


async def _users_view(page: int):
    total = await db.count_users()
    pages = max(1, -(-total // USERS_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    users = await db.list_users(page * USERS_PER_PAGE, USERS_PER_PAGE)
    lines = [f"👥 <b>Foydalanuvchilar</b> — jami {total} ta (sahifa {page + 1}/{pages})\n"
             "Premium berish uchun foydalanuvchini tanlang yoki qidiring."]
    rows = []
    for u in users:
        label = (u.get("full_name") or u.get("username") or str(u["telegram_id"]))[:28]
        rows.append([_btn(f"{'💎 ' if u['is_premium'] else ''}{label}", f"usr:{u['telegram_id']}")])
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"adm_users:{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"adm_users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_btn("🔎 ID / @username bo'yicha qidirish", "usr_search")])
    rows.append([_btn("⬅️ Orqaga", "admin")])
    return "\n".join(lines), _kb(rows)


@router.callback_query(F.data.startswith("adm_users:"))
async def users_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    text, kb = await _users_view(int(callback.data.split(":")[1]))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


async def _user_card(user: dict):
    uid = user["telegram_id"]
    until = await db.get_premium_until(uid)
    used = await db.count_checks_month(uid)
    status = f"💎 Premium: <b>{_local(until)}</b> gacha" if until else "🆓 Bepul reja"
    if is_admin(uid):
        status += " • 👮 admin"
    text = f"👤 {_who(user)}\n{status}\nBu oy tekshiruvlar: <b>{used}</b>" + ("" if until else f" / {FREE_MONTHLY_LIMIT}")
    rows = [[_btn("💎 Premium berish / uzaytirish", f"pgu:{uid}")]]
    if until:
        rows.append([_btn("➖ Premiumni bekor qilish", f"usr_rv:{uid}")])
    rows.append([_btn("⬅️ Ro'yxat", "adm_users:0")])
    return text, _kb(rows)


@router.callback_query(F.data.startswith("usr:"))
async def user_open(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    user = await db.find_user(callback.data.split(":")[1])
    text, kb = await _user_card(user)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("pgu:"))
async def user_grant(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    user = await db.find_user(callback.data.split(":")[1])
    await callback.answer()
    await _ask_days(callback.message, state, user)


@router.callback_query(F.data.startswith("usr_rv:"))
async def user_revoke_ask(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    uid = int(callback.data.split(":")[1])
    user = await db.find_user(str(uid))
    await callback.message.edit_text(
        f"👤 {_who(user)}\n\nPremiumni bekor qilamizmi?",
        parse_mode="HTML",
        reply_markup=_kb([[_btn("✅ Ha, bekor qilish", f"prv:{uid}"), _btn("❌ Yo'q", f"usr:{uid}")]]),
    )
    await callback.answer()


@router.callback_query(F.data == "usr_search")
async def user_search_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
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
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    s = await db.get_stats()
    text = (
        "📈 <b>Statistika</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{s['users']}</b> (bugun yangi: {s['new_today']})\n"
        f"💎 Faol premium: <b>{s['premium']}</b>\n\n"
        f"🎙 Speaking tekshiruvlari: <b>{s['speaking']}</b>\n"
        f"✍️ Writing tekshiruvlari: <b>{s['writing']}</b>\n"
        f"📅 Bugun jami: <b>{s['today']}</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_kb([[_btn("⬅️ Orqaga", "admin")]]))
    await callback.answer()
