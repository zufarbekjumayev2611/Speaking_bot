"""To'lovni tasdiqlash: o'quvchi kartaga to'lab, chekini botga yuboradi -> chek «💎 Premium» huquqi bor adminlarga
boradi -> admin tarifni tanlab «✅ Tasdiqlash»ni bosadi -> o'quvchiga obuna (30 kun) avtomatik beriladi.

Karta raqami: Boss «⚙️ Sozlamalar» -> «💳 Karta raqami» (bazada saqlanadi, qayta deploy kerak emas);
yo'q bo'lsa - Render'dagi CARD_NUMBER."""
import html
import logging
import os
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import access
import config
import db
import plans
import roles
from config import is_admin

log = logging.getLogger("payments")
router = Router()
router.message.filter(F.chat.type == "private")

CARD_KEY = "card_number"
PAY_DAYS = plans.PERIOD_DAYS
_deciding: set[int] = set()  # bir chekni ikki admin bir vaqtda tasdiqlamasin


class PayCheck(StatesGroup):
    photo = State()


class CardEdit(StatesGroup):
    text = State()


class ContactEdit(StatesGroup):
    text = State()


class RejectReason(StatesGroup):
    text = State()


# Chekni rad etish sabablari: kod -> (tugma, o'quvchiga yuboriladigan matn; {price} - tanlagan tarifi narxi)
REJECT_REASONS = {
    "low": ("💸 Kam to'lov qilingan",
            "To'lov summasi kam. Siz tanlagan {plan} tarifi narxi — <b>{price}</b>. Qolgan farqni o'sha kartaga "
            "o'tkazing va yangi chekni yuboring — admin avvalgi to'lovingiz bilan birga hisoblaydi."),
    "nomoney": ("❓ Pul tushmagan", "Bu chek bo'yicha kartaga pul tushmagan. To'lovni tekshirib, qaytadan chek yuboring."),
    "bad": ("🧾 Chek noto'g'ri / o'qilmaydi",
            "Chek rasmi noto'g'ri yoki o'qib bo'lmaydi. Summa, sana va karta raqami ko'rinadigan aniq skrinshot yuboring."),
    "dup": ("🔁 Bu chek avval yuborilgan", "Bu chek avval ham yuborilgan. Yangi to'lov qilgan bo'lsangiz, o'sha chekni yuboring."),
}


def e(value) -> str:
    return html.escape(str(value or ""), quote=False)


def card_text() -> str:
    return db.setting(CARD_KEY) or os.getenv("CARD_NUMBER", "").strip()


def card_block() -> str:
    """Tarif sahifasidagi to'lov bloki."""
    card = card_text()
    if card:
        where = f"💳 <b>To'lov uchun karta:</b>\n<code>{e(card)}</code>\nTarif narxini shu kartaga o'tkazing."
    else:
        where = f"💳 <b>To'lov:</b> karta raqamini admindan so'rang — {e(db.admin_contact())}."
    return (f"{where}\n\nTo'lagach, pastdagi <b>«✅ To'lovni tasdiqlash»</b> tugmasini bosing va chek rasmini "
            "yuboring — admin tasdiqlashi bilan obuna avtomatik yoqiladi.")


def pay_buttons() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="✅ To'lovni tasdiqlash (chek yuborish)", callback_data="pay")],
            [InlineKeyboardButton(text=f"✍️ Adminga yozish ({db.admin_contact()})", url=db.admin_link())]]


RESTART_NOTE = ("🔄 <b>Limitingiz tugagan</b> — to'lov tasdiqlangach obuna <b>hozirdan yangidan</b> boshlanadi: "
                "yangi 30 kun va to'liq limit (eski obunaning qolgan kunlari hisobga olinmaydi).")


def renew_button() -> InlineKeyboardButton:
    """Limiti tugagan obunachi uchun: qayta to'lov - yangi 30 kun va to'liq limit hoziroq."""
    return InlineKeyboardButton(text="🔄 Yangi 30 kun boshlash", callback_data="pay")


def _plan_line(plan: str) -> str:
    return f"{plans.name(plan)} — {plans.money(plans.price_for(plan))}"


# ---------------------------------------------------------------- o'quvchi

@router.callback_query(F.data == "pay")
@router.message(Command("tolov", "tolovni_tasdiqlash"))
async def pay_start(event, state: FSMContext):
    message = event.message if isinstance(event, CallbackQuery) else event
    uid = event.from_user.id
    await state.clear()
    if await db.pending_payment(uid):
        text = "⏳ Oldingi chekingiz adminlar tomonidan ko'rib chiqilmoqda. Tasdiqlangach sizga xabar keladi."
        if isinstance(event, CallbackQuery):
            return await event.answer(text, show_alert=True)
        return await message.answer(text)
    current = (await db.get_premium(uid) or {}).get("plan")
    rows = [[InlineKeyboardButton(text=_plan_line(p), callback_data=f"pay_p:{p}")] for p in plans.on_sale(current)]
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="pay_x")])
    card = card_text()
    restart = await access.limit_reached(uid)
    await message.answer(
        "✅ <b>To'lovni tasdiqlash</b>\n━━━━━━━━━━━━━━━━\n"
        + (f"{RESTART_NOTE}\n\n" if restart else "")
        + (f"💳 Karta: <code>{e(card)}</code>\n\n" if card else "")
        + "Qaysi tarif uchun to'lov qildingiz? 👇",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "pay_x")
async def pay_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Bekor qilindi.")
    await callback.answer()


@router.callback_query(F.data.startswith("pay_p:"))
async def pay_plan(callback: CallbackQuery, state: FSMContext):
    plan = callback.data.split(":")[1]
    if plan not in plans.on_sale((await db.get_premium(callback.from_user.id) or {}).get("plan")):
        return await callback.answer()
    await state.set_state(PayCheck.photo)
    await state.update_data(plan=plan)
    await callback.message.edit_text(
        f"Tarif: <b>{e(_plan_line(plan))}</b>\n\n📸 Endi to'lov <b>chekining rasmini</b> (skrinshot yoki PDF) shu yerga "
        "yuboring. Bekor qilish: /cancel", parse_mode="HTML")
    await callback.answer()


def _admin_kb(payment_id: int, chosen: str) -> InlineKeyboardMarkup:
    ok = [InlineKeyboardButton(text=f"{'⭐ ' if p == chosen else ''}✅ {plans.PLANS[p]['name']}",
                               callback_data=f"pay_ok:{payment_id}:{p}") for p in plans.on_sale(chosen)]
    return InlineKeyboardMarkup(inline_keyboard=[ok, [InlineKeyboardButton(text="❌ Rad etish", callback_data=f"pay_no:{payment_id}")]])


@router.message(PayCheck.photo, F.photo | F.document)
async def pay_receipt(message: Message, state: FSMContext):
    plan = (await state.get_data()).get("plan")
    if plan not in plans.PAID:
        await state.clear()
        return await message.answer("Jarayon uzilib qolgan. «🚀 Tarifni yaxshilash» dan qaytadan boshlang.")
    if message.photo:
        file_id, ftype = message.photo[-1].file_id, "photo"
    else:
        file_id, ftype = message.document.file_id, "document"
    await state.clear()
    pid = await db.create_payment(message.from_user.id, plan, file_id, ftype)
    u = message.from_user
    handle = f" @{e(u.username)}" if u.username else ""
    caption = (f"💳 <b>Yangi to'lov cheki #{pid}</b>\n"
               f"👤 {e(u.full_name)}{handle} (<code>{u.id}</code>)\n"
               f"📦 Tanlagan tarifi: <b>{e(_plan_line(plan))}</b>\n"
               + await _restart_line(u.id) + "\n"
               f"Pul tushganini tekshiring va summaga mos tarifni bosing — obuna {PAY_DAYS} kunga beriladi.")
    sent = 0
    for admin_id in config.all_admin_ids():
        if not roles.can(admin_id, "premium"):
            continue
        try:
            send = message.bot.send_photo if ftype == "photo" else message.bot.send_document
            await send(admin_id, file_id, caption=caption, parse_mode="HTML", reply_markup=_admin_kb(pid, plan))
            sent += 1
        except Exception:
            log.warning("Chekni adminga (%s) yuborib bo'lmadi", admin_id, exc_info=True)
    if not sent:
        log.error("Chek #%s hech bir adminga yetmadi", pid)
    await message.answer(
        f"✅ Chekingiz qabul qilindi (#{pid}) va adminlarga yuborildi.\n"
        "Tekshirilgach obuna <b>avtomatik</b> yoqiladi va sizga xabar keladi. Savol bo'lsa: "
        f"{e(db.admin_contact())}", parse_mode="HTML")


@router.message(PayCheck.photo, ~F.text.startswith("/"))
async def pay_receipt_wrong(message: Message):
    await message.answer("📸 Chekni <b>rasm</b> (skrinshot) yoki fayl qilib yuboring. Bekor qilish: /cancel", parse_mode="HTML")


# ---------------------------------------------------------------- admin

@router.callback_query(F.data.startswith("pchk:"))
async def pay_card_again(callback: CallbackQuery):
    """Boss panelidagi «Cheklar» ro'yxatidan: chek rasmini tasdiqlash tugmalari bilan qayta ko'rsatish."""
    if not is_admin(callback.from_user.id) or not roles.can(callback.from_user.id, "premium"):
        return await callback.answer("🔒 Bu amal sizga yopiq.", show_alert=True)
    pay = await db.get_payment(int(callback.data.split(":")[1]))
    if not pay or pay["status"] != "pending":
        return await callback.answer("Bu chek allaqachon ko'rib chiqilgan.", show_alert=True)
    u = await db.get_user(pay["telegram_id"]) or {}
    handle = f" @{e(u['username'])}" if u.get("username") else ""
    caption = (f"💳 <b>To'lov cheki #{pay['id']}</b>\n👤 {e(u.get('full_name') or pay['telegram_id'])}{handle} "
               f"(<code>{pay['telegram_id']}</code>)\n📦 Tanlagan tarifi: <b>{e(_plan_line(pay['plan']))}</b>\n"
               + await _restart_line(pay["telegram_id"]) + "\n"
               f"Pul tushganini tekshiring va summaga mos tarifni bosing — obuna {PAY_DAYS} kunga beriladi.")
    send = callback.message.answer_photo if pay["file_type"] == "photo" else callback.message.answer_document
    await send(pay["file_id"], caption=caption, parse_mode="HTML", reply_markup=_admin_kb(pay["id"], pay["plan"]))
    await callback.answer()


async def _restart_line(user_id: int) -> str:
    """Admin uchun: tasdiqlansa obuna uzaytiriladimi yoki hozirdan qayta boshlanadimi."""
    if await access.limit_reached(user_id):
        return "🔄 Limiti tugagan — tasdiqlansa obuna <b>hozirdan yangi 30 kunga</b> boshlanadi (to'liq limit).\n"
    if await db.get_premium(user_id):
        return "➕ Faol obunasi bor — 30 kun tugash sanasiga qo'shiladi.\n"
    return ""


async def _close_card(callback: CallbackQuery, line: str):
    await _close_msg(callback.bot, callback.message.chat.id, callback.message.message_id,
                     callback.message.caption or "", line)


async def _close_msg(bot, chat_id: int, message_id: int, old_caption: str, line: str):
    try:
        await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=e(old_caption) + f"\n\n{line}",
                                       parse_mode="HTML", reply_markup=None)
    except Exception:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except Exception:
            pass


async def _decided(callback: CallbackQuery, pid: int) -> dict | None:
    """Chek hali ko'rib chiqilmagan bo'lsa - uni qaytaradi; aks holda adminga kim hal qilganini aytadi."""
    if not is_admin(callback.from_user.id) or not roles.can(callback.from_user.id, "premium"):
        await callback.answer("🔒 Bu amal sizga yopiq.", show_alert=True)
        return None
    pay = await db.get_payment(pid)
    if not pay:
        await callback.answer("Chek topilmadi.", show_alert=True)
        return None
    if pay["status"] != "pending" or pid in _deciding:
        what = {"approved": f"tasdiqlangan ({plans.name(pay.get('decided_plan') or '')})",
                "rejected": "rad etilgan"}.get(pay["status"], "ko'rib chiqilmoqda")
        await callback.answer(f"Bu chek allaqachon {what}.", show_alert=True)
        await _close_card(callback, f"ℹ️ Allaqachon {e(what)}.")
        return None
    return pay


@router.callback_query(F.data.startswith("pay_ok:"))
async def pay_approve(callback: CallbackQuery):
    _, pid, plan = callback.data.split(":")
    pid = int(pid)
    if plan not in plans.PAID:
        return await callback.answer()
    pay = await _decided(callback, pid)
    if not pay:
        return
    _deciding.add(pid)
    try:
        restart = await access.limit_reached(pay["telegram_id"])  # limit tugagan - yangi 30 kun hozirdan
        if not await db.decide_payment(pid, "approved", callback.from_user.id, plan):
            await _close_card(callback, "ℹ️ Bu chekni boshqa admin allaqachon ko'rib chiqdi.")
            return await callback.answer("Bu chek allaqachon ko'rib chiqilgan.", show_alert=True)
        until = await db.grant_premium(pay["telegram_id"], PAY_DAYS, str(plans.price_for(plan)),
                                       callback.from_user.id, plan, restart=restart)
    finally:
        _deciding.discard(pid)
    from premium import _local  # premium.py bu modulni import qiladi - aylana importdan qochish
    try:
        await callback.bot.send_message(
            pay["telegram_id"],
            f"🎉 <b>To'lovingiz tasdiqlandi!</b>\n"
            + ("🔄 Obunangiz yangidan boshlandi — limit to'liq yangilandi.\n" if restart else "")
            + f"{e(plans.name(plan))} faollashtirildi — "
            f"<b>{_local(until)}</b> gacha.\nHar 30 kunda: {plans.limits_text(plan)}"
            + (" • 2 ekspert baholashi" if plans.raters_for(plan) > 1 else "") + ".\n\n«🎙 Konuşma» va «✍️ Yazma» — omad! 🚀",
            parse_mode="HTML")
        note = ""
    except Exception:
        note = " (o'quvchiga xabar yetmadi)"
    await _close_card(callback, f"✅ <b>Tasdiqlandi</b>: {e(plans.name(plan))}, {_local(until)} gacha — "
                                f"{e(callback.from_user.full_name)}{note}")
    await callback.answer("Obuna berildi ✅")


def _reasons_kb(pid: int) -> InlineKeyboardMarkup:
    B = InlineKeyboardButton
    rows = [[B(text=label, callback_data=f"pay_nr:{pid}:{code}")] for code, (label, _) in REJECT_REASONS.items()]
    rows.append([B(text="✍️ Sababni o'zim yozaman", callback_data=f"pay_nr:{pid}:own")])
    rows.append([B(text="⬅️ Orqaga", callback_data=f"pay_nb:{pid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("pay_no:"))
async def pay_reject(callback: CallbackQuery):
    """«❌ Rad etish» - avval sabab tanlanadi (o'quvchiga nima uchun rad etilgani aytiladi)."""
    pid = int(callback.data.split(":")[1])
    if not await _decided(callback, pid):
        return
    await callback.message.edit_reply_markup(reply_markup=_reasons_kb(pid))
    await callback.answer("Rad etish sababini tanlang")


@router.callback_query(F.data.startswith("pay_nb:"))
async def pay_reject_back(callback: CallbackQuery, state: FSMContext):
    pid = int(callback.data.split(":")[1])
    pay = await _decided(callback, pid)
    if not pay:
        return
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=_admin_kb(pid, pay["plan"]))
    await callback.answer()


@router.callback_query(F.data.startswith("pay_nr:"))
async def pay_reject_reason(callback: CallbackQuery, state: FSMContext):
    _, pid, code = callback.data.split(":")
    pid = int(pid)
    pay = await _decided(callback, pid)
    if not pay:
        return
    if code == "own":
        await state.clear()
        await state.set_state(RejectReason.text)
        await state.update_data(pid=pid, card_chat=callback.message.chat.id, card_msg=callback.message.message_id,
                                card_caption=callback.message.caption or "")
        await callback.message.answer(
            f"✍️ Chek #{pid} — o'quvchiga rad etish sababini yozing (masalan: <i>30 000 so'm to'lagansiz, Standard "
            f"narxi 59 000 so'm — farqini to'lang</i>).\nBekor qilish: /cancel", parse_mode="HTML")
        return await callback.answer()
    if code not in REJECT_REASONS:
        return await callback.answer()
    plan = pay.get("plan") or "standard"
    reason = REJECT_REASONS[code][1].format(plan=e(plans.name(plan)), price=plans.money(plans.price_for(plan)))
    line = await _reject(callback.bot, pay, callback.from_user, reason)
    await _close_card(callback, line)
    await callback.answer("Rad etildi")


@router.message(RejectReason.text, ~F.text.startswith("/"))
async def pay_reject_own(message: Message, state: FSMContext):
    from bot import MENU_BUTTONS  # bot.py bu modulni import qiladi - aylana importdan qochish
    if not is_admin(message.from_user.id) or not roles.can(message.from_user.id, "premium"):
        return await state.clear()
    text = (message.text or "").strip()
    if text in MENU_BUTTONS:  # menyu tugmasi sabab bo'lib ketmasin
        await state.clear()
        return await message.answer("Rad etish bekor qilindi — chek hali ko'rib chiqilmagan.")
    if not text or len(text) > 500:
        return await message.answer("Sababni matn bilan yozing (500 belgigacha). Bekor qilish: /cancel")
    data = await state.get_data()
    await state.clear()
    pay = await db.get_payment(data.get("pid") or 0)
    if not pay or pay["status"] != "pending":
        return await message.answer("Bu chek allaqachon ko'rib chiqilgan.")
    line = await _reject(message.bot, pay, message.from_user, e(text))
    await _close_msg(message.bot, data["card_chat"], data["card_msg"], data.get("card_caption", ""), line)
    await message.answer(f"✅ Chek #{pay['id']} rad etildi, sabab o'quvchiga yuborildi.")


async def _reject(bot, pay: dict, admin, reason_html: str) -> str:
    """Chekni rad etadi va o'quvchiga sababini yuboradi; chek kartasi uchun qator qaytaradi."""
    pid = pay["id"]
    if not await db.decide_payment(pid, "rejected", admin.id, reason=re.sub(r"<[^>]+>", "", reason_html)):
        return "ℹ️ Bu chekni boshqa admin allaqachon ko'rib chiqdi."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Chekni qayta yuborish", callback_data="pay")],
        [InlineKeyboardButton(text=f"✍️ Adminga yozish ({db.admin_contact()})", url=db.admin_link())],
    ])
    note = ""
    try:
        await bot.send_message(
            pay["telegram_id"],
            f"❌ <b>To'lov cheki #{pid} tasdiqlanmadi.</b>\n\n📝 Sabab: {reason_html}\n\n"
            "To'g'rilab, chekni qayta yuboring 👇", parse_mode="HTML", reply_markup=kb)
    except Exception:
        note = " (o'quvchiga xabar yetmadi)"
    return f"❌ <b>Rad etildi</b> — {e(admin.full_name)}{note}\n📝 {reason_html}"


# ---------------------------------------------------------------- Boss: karta raqami

@router.callback_query(F.data == "card_edit")
async def card_edit(callback: CallbackQuery, state: FSMContext):
    if not config.is_owner(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await state.set_state(CardEdit.text)
    now = card_text() or "kiritilmagan"
    await callback.message.answer(
        f"💳 <b>Karta raqami</b> — o'quvchi tarif sahifasida va chek yuborishda ko'radi.\n\nHozir: <code>{e(now)}</code>\n\n"
        "Yangisini yozing, masalan: <code>8600 1234 5678 9012 — Ali Valiyev</code>\n"
        "O'chirish uchun «-». Bekor qilish: /cancel", parse_mode="HTML")
    await callback.answer()


@router.message(CardEdit.text, ~F.text.startswith("/"))  # /cancel - bot.py dagi umumiy bekor qilishga o'tadi
async def card_save(message: Message, state: FSMContext):
    if not config.is_owner(message.from_user.id):
        return await state.clear()
    from bot import MENU_BUTTONS
    if (message.text or "").strip() in MENU_BUTTONS:  # menyu tugmasi matni saqlanib qolmasin
        await state.clear()
        return await message.answer("Karta raqami o'zgartirilmadi.")
    text = " ".join((message.text or "").split())
    if not text or text.startswith("/") or len(text) > 120:
        return await message.answer("Karta raqamini matn bilan yozing (120 belgigacha) yoki «-». Bekor qilish: /cancel")
    await db.set_setting(CARD_KEY, "" if text == "-" else text)
    await state.clear()
    from premium import _settings_view
    view, kb = await _settings_view()
    await message.answer("✅ Saqlandi.")
    await message.answer(view, parse_mode="HTML", reply_markup=kb)


# ---------------------------------------------------------------- Boss: o'quvchilar yozadigan admin

@router.callback_query(F.data == "ac_edit")
async def contact_edit(callback: CallbackQuery, state: FSMContext):
    if not config.is_owner(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    await state.set_state(ContactEdit.text)
    await callback.message.answer(
        f"👤 <b>Admin bilan bog'lanish</b> — o'quvchi «Profil» va «Tarifni yaxshilash» bo'limlarida shu adminga "
        f"yozadi.\n\nHozir: <b>{e(db.admin_contact())}</b>\n\nYangi username'ni yozing (masalan <code>@ali_admin</code>). "
        "Render'dagi standartga qaytarish uchun «-». Bekor qilish: /cancel", parse_mode="HTML")
    await callback.answer()


@router.message(ContactEdit.text, ~F.text.startswith("/"))
async def contact_save(message: Message, state: FSMContext):
    if not config.is_owner(message.from_user.id):
        return await state.clear()
    from bot import MENU_BUTTONS
    if (message.text or "").strip() in MENU_BUTTONS:  # menyu tugmasi matni saqlanib qolmasin
        await state.clear()
        return await message.answer("Admin username o'zgartirilmadi.")
    raw = (message.text or "").strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
    raw = raw.strip().lstrip("@").strip("/")
    if raw != "-" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", raw or ""):
        return await message.answer("Telegram username'ni yozing, masalan @ali_admin (5–32 belgi: harf, raqam, _). "
                                    "Bekor qilish: /cancel")
    await db.set_setting("admin_contact", "" if raw == "-" else raw)
    await state.clear()
    from premium import _settings_view
    view, kb = await _settings_view()
    await message.answer(f"✅ Saqlandi: o'quvchilar endi {e(db.admin_contact())} ga yozadi.")
    await message.answer(view, parse_mode="HTML", reply_markup=kb)
