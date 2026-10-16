"""To'lovni tasdiqlash: o'quvchi kartaga to'lab, chekini botga yuboradi -> chek «💎 Premium» huquqi bor adminlarga
boradi -> admin tarifni tanlab «✅ Tasdiqlash»ni bosadi -> o'quvchiga obuna (30 kun) avtomatik beriladi.

Karta raqami: Boss «⚙️ Sozlamalar» -> «💳 Karta raqami» (bazada saqlanadi, qayta deploy kerak emas);
yo'q bo'lsa - Render'dagi CARD_NUMBER."""
import html
import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

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
        where = f"💳 <b>To'lov:</b> karta raqamini admindan so'rang — {e(config.ADMIN_CONTACT)}."
    return (f"{where}\n\nTo'lagach, pastdagi <b>«✅ To'lovni tasdiqlash»</b> tugmasini bosing va chek rasmini "
            "yuboring — admin tasdiqlashi bilan obuna avtomatik yoqiladi.")


def pay_buttons() -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="✅ To'lovni tasdiqlash (chek yuborish)", callback_data="pay")],
            [InlineKeyboardButton(text=f"✍️ Adminga yozish ({config.ADMIN_CONTACT})",
                                  url=f"https://t.me/{config.ADMIN_CONTACT.lstrip('@')}")]]


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
    rows = [[InlineKeyboardButton(text=_plan_line(p), callback_data=f"pay_p:{p}")] for p in plans.PAID]
    rows.append([InlineKeyboardButton(text="❌ Bekor qilish", callback_data="pay_x")])
    card = card_text()
    await message.answer(
        "✅ <b>To'lovni tasdiqlash</b>\n━━━━━━━━━━━━━━━━\n"
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
    if plan not in plans.PAID:
        return await callback.answer()
    await state.set_state(PayCheck.photo)
    await state.update_data(plan=plan)
    await callback.message.edit_text(
        f"Tarif: <b>{e(_plan_line(plan))}</b>\n\n📸 Endi to'lov <b>chekining rasmini</b> (skrinshot yoki PDF) shu yerga "
        "yuboring. Bekor qilish: /cancel", parse_mode="HTML")
    await callback.answer()


def _admin_kb(payment_id: int, chosen: str) -> InlineKeyboardMarkup:
    ok = [InlineKeyboardButton(text=f"{'⭐ ' if p == chosen else ''}✅ {plans.PLANS[p]['name']}",
                               callback_data=f"pay_ok:{payment_id}:{p}") for p in plans.PAID]
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
               f"📦 Tanlagan tarifi: <b>{e(_plan_line(plan))}</b>\n\n"
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
        f"{e(config.ADMIN_CONTACT)}", parse_mode="HTML")


@router.message(PayCheck.photo, ~F.text.startswith("/"))
async def pay_receipt_wrong(message: Message):
    await message.answer("📸 Chekni <b>rasm</b> (skrinshot) yoki fayl qilib yuboring. Bekor qilish: /cancel", parse_mode="HTML")


# ---------------------------------------------------------------- admin

async def _close_card(callback: CallbackQuery, line: str):
    try:
        old = callback.message.caption or ""
        await callback.message.edit_caption(caption=e(old) + f"\n\n{line}", parse_mode="HTML", reply_markup=None)
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
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
        await db.decide_payment(pid, "approved", callback.from_user.id, plan)
        until = await db.grant_premium(pay["telegram_id"], PAY_DAYS, str(plans.price_for(plan)),
                                       callback.from_user.id, plan)
    finally:
        _deciding.discard(pid)
    from premium import _local  # premium.py bu modulni import qiladi - aylana importdan qochish
    try:
        await callback.bot.send_message(
            pay["telegram_id"],
            f"🎉 <b>To'lovingiz tasdiqlandi!</b>\n{e(plans.name(plan))} faollashtirildi — "
            f"<b>{_local(until)}</b> gacha.\nHar 30 kunda: {plans.limits_text(plan)}"
            + (" • 2 ekspert baholashi" if plans.raters_for(plan) > 1 else "") + ".\n\n«🎙 Konuşma» va «✍️ Yazma» — omad! 🚀",
            parse_mode="HTML")
        note = ""
    except Exception:
        note = " (o'quvchiga xabar yetmadi)"
    await _close_card(callback, f"✅ <b>Tasdiqlandi</b>: {e(plans.name(plan))}, {_local(until)} gacha — "
                                f"{e(callback.from_user.full_name)}{note}")
    await callback.answer("Obuna berildi ✅")


@router.callback_query(F.data.startswith("pay_no:"))
async def pay_reject(callback: CallbackQuery):
    pid = int(callback.data.split(":")[1])
    pay = await _decided(callback, pid)
    if not pay:
        return
    await db.decide_payment(pid, "rejected", callback.from_user.id)
    try:
        await callback.bot.send_message(
            pay["telegram_id"],
            f"❌ To'lov cheki #{pid} tasdiqlanmadi. Pul tushmagan yoki chek noto'g'ri bo'lishi mumkin.\n"
            f"Savol bo'lsa, adminga yozing: {e(config.ADMIN_CONTACT)}", parse_mode="HTML")
    except Exception:
        pass
    await _close_card(callback, f"❌ <b>Rad etildi</b> — {e(callback.from_user.full_name)}")
    await callback.answer("Rad etildi")


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


@router.message(CardEdit.text)
async def card_save(message: Message, state: FSMContext):
    if not config.is_owner(message.from_user.id):
        return await state.clear()
    text = " ".join((message.text or "").split())
    if not text or text.startswith("/") or len(text) > 120:
        return await message.answer("Karta raqamini matn bilan yozing (120 belgigacha) yoki «-». Bekor qilish: /cancel")
    await db.set_setting(CARD_KEY, "" if text == "-" else text)
    await state.clear()
    from premium import _settings_view
    view, kb = await _settings_view()
    await message.answer("✅ Saqlandi.")
    await message.answer(view, parse_mode="HTML", reply_markup=kb)
