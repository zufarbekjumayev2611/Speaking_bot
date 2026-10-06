"""Botning barqaror ishlashi uchun umumiy qatlam:

- har bir yozgan odam bazaga yoziladi (faqat /start bosganlar emas) - bot uni "taniydi";
- handler ichida xato bo'lsa, foydalanuvchi javobsiz qolmaydi, tugma "aylanib" qolmaydi;
- eskirgan / tanilmagan tugma bosilsa - tushuntirish chiqadi;
- bot ishga tushganda adminlarga qaysi server va qaysi baza ishlatilayotgani yuboriladi;
- shu bot tokeni bilan boshqa joyda ham bot ishga tushirilgan bo'lsa (TelegramConflictError -
  xabarlar ikki nusxa orasida bo'linib, "bir safar bor, bir safar yo'q" holati) - adminlarga ogohlantirish;
- /status (admin) - server, baza va asosiy sonlar.
"""
import asyncio
import logging
import os
import re
import socket
import time

from aiogram import BaseMiddleware, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

import db
import config
from config import WEBAPP_URL, WEBAPP_URL_ENV, WEBAPP_URL_IGNORED, all_admin_ids, is_admin

log = logging.getLogger("ops")

STARTED = time.time()
INSTANCE = os.getenv("RENDER_INSTANCE_ID") or socket.gethostname()
VERSION = (os.getenv("RENDER_GIT_COMMIT") or "")[:7] or "lokal"
TOUCH_EVERY = 1800  # foydalanuvchi ma'lumotini bazada yangilash oralig'i (s)


def warnings() -> list[str]:
    """Render sozlamalaridagi xavfli holatlar (ikki bot, ma'lumot o'chishi)."""
    out = []
    if WEBAPP_URL_IGNORED:
        out.append(f"⚠️ Render'dagi WEBAPP_URL (<code>{WEBAPP_URL_ENV}</code>) bu servisning manzili emas — e'tiborsiz "
                   "qoldirildi. U eski servisga qarab qolgan bo'lsa, eski servisni o'chiring va WEBAPP_URL ni olib tashlang.")
    if os.getenv("RENDER") and db.backend_name().startswith("SQLite"):
        parent = os.path.dirname(os.path.abspath(config.DB_PATH))
        if not os.path.ismount(parent):
            out.append("⚠️ Turso ulanmagan va disk yo'q: ma'lumotlar har deploy/restartda O'CHADI. Render → Environment'da "
                       "TURSO_DATABASE_URL va TURSO_AUTH_TOKEN nomlarini tekshiring.")
    if not config.ADMIN_IDS:
        out.append("⚠️ ADMIN_IDS bo'sh — asosiy admin yo'q.")
    return out


def instance_line() -> str:
    lines = [f"Server: <code>{INSTANCE}</code> • versiya: <code>{VERSION}</code> • baza: <b>{db.backend_name()}</b>",
             f"Mini app: <code>{WEBAPP_URL}</code> • adminlar: <code>{', '.join(map(str, config.ADMIN_IDS)) or '-'}</code>"]
    return "\n".join(lines + warnings())


async def _notify_admins(bot, text: str):
    for admin_id in all_admin_ids():
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass


# ---------------------------------------------------------------- foydalanuvchini tanish

class UserTouchMiddleware(BaseMiddleware):
    """Botga yozgan har bir odam bazada bo'lsin (ism / username yangilanib turadi)."""

    def __init__(self):
        self._seen: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and not user.is_bot and time.time() - self._seen.get(user.id, 0) > TOUCH_EVERY:
            try:
                await db.save_user(user.id, user.full_name, user.username)
                self._seen[user.id] = time.time()
            except Exception:
                log.exception("Foydalanuvchini saqlab bo'lmadi (%s)", user.id)
        return await handler(event, data)


# ---------------------------------------------------------------- xatolar

async def on_error(event: ErrorEvent):
    exc, upd = event.exception, event.update
    cq, msg = upd.callback_query, upd.message
    if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc):
        # bir xil tugma ikki marta bosildi - ekrandagi matn allaqachon to'g'ri
        if cq:
            try:
                await cq.answer()
            except Exception:
                pass
        return True
    log.error("Handler xatosi (update %s)", upd.update_id, exc_info=exc)
    text = "⚠️ Xatolik yuz berdi. Qayta urinib ko'ring yoki /start bosing."
    try:
        if cq:
            try:
                await cq.answer(text, show_alert=True)
            except Exception:  # so'rov allaqachon javob olgan yoki eskirgan - oddiy xabar bilan aytamiz
                await cq.bot.send_message(cq.from_user.id, text)
        elif msg and msg.chat.type == "private":
            await msg.answer(text)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- oxirgi navbat: /status va tanilmagan narsalar

fallback = Router()
fallback.message.filter(F.chat.type == "private")


@fallback.message(Command("status"))
async def status(message: Message):
    if not is_admin(message.from_user.id):
        return
    users = (await db._fetchone("SELECT COUNT(*) AS c FROM users"))["c"]
    exams = await db._fetchall(
        "SELECT COALESCE(kind, 'speaking') AS kind, SUM(is_active) AS open, COUNT(*) AS total FROM exams GROUP BY 1"
    )
    by_kind = {r["kind"]: r for r in exams}
    sp, wr = by_kind.get("speaking", {}), by_kind.get("writing", {})
    up = int(time.time() - STARTED)
    await message.answer(
        "🩺 <b>Holat</b>\n"
        f"{instance_line()}\n"
        f"Ishlayapti: {up // 3600} soat {up % 3600 // 60} daqiqa\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"🎙 Konuşma testlari: <b>{sp.get('open') or 0}</b> ochiq / {sp.get('total') or 0}\n"
        f"✍️ Yazma mavzulari: <b>{wr.get('open') or 0}</b> ochiq / {wr.get('total') or 0}\n\n"
        "<i>/status ni bir necha marta yuboring: «Server» har safar bir xil bo'lishi kerak. Har xil bo'lsa — "
        "bot ikki joyda ishga tushirilgan, bittasini to'xtating.</i>",
        parse_mode="HTML",
    )


@fallback.callback_query()
async def unknown_callback(callback: CallbackQuery):
    await callback.answer("Bu tugma eskirgan. Menyudan qaytadan tanlang yoki /start bosing.", show_alert=True)


@fallback.message()
async def unknown_message(message: Message, state: FSMContext):
    from bot import main_keyboard  # bot.py ops'ni import qilmaydi - aylana import yo'q
    if await state.get_state():
        return await message.answer("Yuqoridagi so'rovga javob bering yoki tugmalardan birini tanlang. Bekor qilish: /cancel")
    await message.answer("Bo'limni tanlang 👇", reply_markup=main_keyboard(message.from_user.id))


# ---------------------------------------------------------------- ikkinchi nusxa (TelegramConflictError)

class ConflictWatcher(logging.Handler):
    """aiogram "Failed to fetch updates - TelegramConflictError" takrorlansa - adminlarga xabar (soatiga 1 marta).
    Deploy paytidagi qisqa ustma-ustlik (eski nusxa hali to'xtamagan) hisobga olinmaydi."""

    GRACE = 120      # ishga tushgandan keyingi soniyalar - deploy ustma-ustligi
    WINDOW = 300     # shu oraliqda
    REPEATS = 3      # kamida shuncha conflict bo'lsa - haqiqiy ikkinchi nusxa

    def __init__(self, bot):
        super().__init__(level=logging.ERROR)
        self.bot = bot
        self.last = 0.0
        self.hits: list[float] = []

    def emit(self, record: logging.LogRecord):
        try:
            now = time.time()
            if "TelegramConflictError" not in record.getMessage() or now - STARTED < self.GRACE:
                return
            self.hits = [t for t in self.hits if now - t < self.WINDOW] + [now]
            if len(self.hits) < self.REPEATS or now - self.last < 3600:
                return
            self.last = now
            asyncio.get_running_loop().create_task(_notify_admins(
                self.bot,
                "⚠️ <b>Bot ikki joyda ishga tushirilgan!</b>\nShu token bilan boshqa joyda ham bot ishlayapti "
                "(eski Render servisi, kompyuterdagi nusxa yoki boshqa hosting). Xabarlar ular orasida bo'linib "
                "ketadi — shuning uchun bot ba'zan testlarni ko'rsatadi, ba'zan «yo'q» deydi va tugmalar ishlamaydi.\n"
                f"Faqat bittasini qoldiring.\nBu xabarni yuborgan nusxa — {instance_line()}",
            ))
        except Exception:
            pass


async def startup(bot):
    for w in warnings():
        log.error(re.sub(r"<[^>]+>", "", w))
    await _notify_admins(bot, f"🟢 <b>Bot ishga tushdi</b>\n{instance_line()}\nTekshirish: /status")


async def refresh_loop():
    """Adminlar, bloklanganlar va sozlamalar har daqiqada bazadan qayta o'qiladi
    (masalan, Turso panelida qo'lda o'zgartirilsa - qayta ishga tushirish shart emas)."""
    while True:
        await asyncio.sleep(60)
        try:
            await db.load_admins()
            await db.load_blocked()
            await db.load_settings()
        except Exception:
            log.warning("Keshlarni yangilab bo'lmadi", exc_info=True)


async def drain_background(timeout: float = 25):
    """Bot to'xtayotganda fonda ketayotgan yazma tekshiruvlarini biroz kutadi."""
    from bot import _background
    if _background:
        await asyncio.wait(list(_background), timeout=timeout)


def setup(dp: Dispatcher, bot):
    """main.py dan: barcha routerlardan KEYIN chaqiriladi (fallback oxirida turishi kerak)."""
    dp.update.outer_middleware(UserTouchMiddleware())
    dp.errors.register(on_error)
    dp.include_router(fallback)
    logging.getLogger("aiogram.dispatcher").addHandler(ConflictWatcher(bot))
