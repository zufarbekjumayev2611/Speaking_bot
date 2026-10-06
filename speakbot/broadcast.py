"""📣 Ommaviy xabar (admin) va premium tugashi haqida avtomatik eslatma."""
import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db
import plans
from config import is_admin
from premium import _local

log = logging.getLogger("broadcast")

router = Router()
router.message.filter(F.chat.type == "private")

AUDIENCES = {
    "all": "👥 Hammaga",
    "free": "🆓 Bepul foydalanuvchilarga",
    "standard": "⭐ Standard obunachilarga",
    "pro": "💎 Pro obunachilarga",
}
SEND_DELAY = 0.05  # soniyasiga ~20 ta xabar (Telegram limiti ~30)
_running = False


class Broadcast(StatesGroup):
    text = State()


def _kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _btn(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


@router.callback_query(F.data == "adm_bc")
async def bc_menu(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    await state.clear()
    rows = []
    for key, label in AUDIENCES.items():
        rows.append([_btn(f"{label} ({len(await db.broadcast_ids(key))})", f"bc_a:{key}")])
    rows.append([_btn("⬅️ Orqaga", "admin")])
    await callback.message.edit_text(
        "📣 <b>Ommaviy xabar</b>\n\nKimga yuboramiz? (Bloklanganlarga yuborilmaydi.)", parse_mode="HTML", reply_markup=_kb(rows)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bc_a:"))
async def bc_audience(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    key = callback.data.split(":")[1]
    if key not in AUDIENCES:
        return await callback.answer()
    if _running:
        return await callback.answer("Oldingi xabar hali yuborilmoqda. Tugashini kuting.", show_alert=True)
    await state.clear()
    await state.set_state(Broadcast.text)
    await state.update_data(audience=key)
    await callback.message.answer(
        f"📣 {AUDIENCES[key]}\n\nYuboriladigan xabar matnini yozing (ko'pi bilan 3500 belgi). Bekor qilish: /cancel"
    )
    await callback.answer()


@router.message(Broadcast.text)
async def bc_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        return await message.answer("Matn yuboring. Bekor qilish: /cancel")
    if len(text) > 3500:
        return await message.answer(f"Matn juda uzun ({len(text)} belgi). 3500 belgidan oshmasin.")
    data = await state.get_data()
    if data.get("audience") not in AUDIENCES:
        await state.clear()
        return await message.answer("Jarayon uzilib qolgan. «📣 Xabar yuborish» bo'limidan qaytadan boshlang.")
    count = len(await db.broadcast_ids(data["audience"]))
    preview = await message.answer(
        f"👀 <b>Ko'rinishi:</b>\n\n{_esc(text)}\n\n— — —\n{AUDIENCES[data['audience']]}: <b>{count}</b> kishiga yuboriladi. Tasdiqlaysizmi?",
        parse_mode="HTML",
        reply_markup=_kb([[_btn("✅ Yuborish", "bc_go"), _btn("❌ Bekor qilish", "admin")]]),
    )
    await state.update_data(bc_text=text, bc_msg=preview.message_id)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@router.callback_query(Broadcast.text, F.data == "bc_go")
async def bc_go(callback: CallbackQuery, state: FSMContext):
    global _running
    if not is_admin(callback.from_user.id):
        return await callback.answer()
    data = await state.get_data()
    if not data.get("bc_text") or data.get("audience") not in AUDIENCES:
        return await callback.answer("Xabar topilmadi. Qaytadan boshlang.", show_alert=True)
    if data.get("bc_msg") != callback.message.message_id:
        return await callback.answer("Bu eski ko'rinish. Eng oxirgi ko'rinishdagi «✅ Yuborish»ni bosing.", show_alert=True)
    if _running:
        return await callback.answer("Oldingi xabar hali yuborilmoqda.", show_alert=True)
    await state.clear()
    ids = await db.broadcast_ids(data["audience"])
    await callback.message.edit_text(f"📤 Yuborish boshlandi: {len(ids)} kishi. Tugagach natijani yozaman.")
    await callback.answer()
    _running = True
    asyncio.create_task(_send_all(callback.bot, callback.from_user.id, ids, data["bc_text"]))


async def _send_all(bot, admin_id: int, ids: list[int], text: str):
    global _running
    sent = failed = 0
    try:
        for uid in ids:
            for attempt in range(3):
                try:
                    await bot.send_message(uid, text)
                    sent += 1
                    break
                except TelegramRetryAfter as err:
                    await asyncio.sleep(err.retry_after + 1)
                except TelegramForbiddenError:  # foydalanuvchi botni to'xtatgan
                    failed += 1
                    break
                except Exception:
                    if attempt == 2:
                        failed += 1
            await asyncio.sleep(SEND_DELAY)
    finally:
        _running = False
    try:
        await bot.send_message(admin_id, f"✅ Yuborish tugadi.\nYetkazildi: {sent}\nYetkazilmadi: {failed}")
    except Exception:
        pass


# ---------- Premium tugashi haqida eslatma (har soatda tekshiriladi) ----------

async def reminder_loop(bot):
    await asyncio.sleep(30)
    while True:
        try:
            for r in await db.due_reminders(3):
                if r["telegram_id"] in config.BLOCKED_IDS:
                    await db.mark_reminded(r["telegram_id"], r["until"])
                    continue
                try:
                    await bot.send_message(
                        r["telegram_id"],
                        f"⏰ {plans.name(r['plan'])} obunangiz <b>{_local(r['until'])}</b> da tugaydi.\n"
                        "Davom ettirish uchun «🚀 Tarifni yaxshilash» bo'limiga qarang yoki admin bilan bog'laning.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass  # foydalanuvchi botni to'xtatgan bo'lishi mumkin - baribir qayta urinmaymiz
                await db.mark_reminded(r["telegram_id"], r["until"])
                await asyncio.sleep(SEND_DELAY)
        except Exception:
            log.exception("Eslatma tsiklida xato")
        await asyncio.sleep(3600)
