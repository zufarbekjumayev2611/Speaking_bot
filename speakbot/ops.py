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
import hashlib
import html
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
INSTANCE = os.getenv("RENDER_INSTANCE_ID") or f"{socket.gethostname()}-{os.getpid()}"
SERVICE = os.getenv("RENDER_SERVICE_NAME") or os.getenv("RENDER_SERVICE_ID") or socket.gethostname()
SERVICE_TYPE = os.getenv("RENDER_SERVICE_TYPE", "")
VERSION = (os.getenv("RENDER_GIT_COMMIT") or "")[:7] or "lokal"
TOUCH_EVERY = 1800  # foydalanuvchi ma'lumotini bazada yangilash oralig'i (s)

# Webhook manzili tokendan hosil qilinadi (tashqaridan topib bo'lmaydi); Telegram har so'rovda maxfiy kalitni yuboradi.
_H = hashlib.sha256(("speakbot-webhook:" + config.BOT_TOKEN).encode()).hexdigest()
WEBHOOK_PATH = f"/tg/{_H[:24]}"
WEBHOOK_SECRET = _H[24:64]
WEBHOOK_URL = WEBAPP_URL + WEBHOOK_PATH
WEBHOOK_CHECK = 60  # webhook joyidami - shuncha soniyada bir tekshiriladi


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


def _db_line() -> str:
    if config.TURSO_URL:
        host = re.sub(r"^\w+://", "", config.TURSO_URL).split("/")[0]
        return f"Turso (<code>{host}</code>)"
    return db.backend_name()


def _mode_text() -> str:
    if config.BOT_MODE == "webhook":
        return "webhook"
    if config.BOT_MODE == "off":
        return "bot o'chirilgan (BOT_MODE=off)"
    return "polling, faol" if leader.active else "polling, kutish rejimida"


def instance_line() -> str:
    kind = f" ({SERVICE_TYPE})" if SERVICE_TYPE else ""
    lines = [f"Servis: <b>{SERVICE}</b>{kind} • versiya: <code>{VERSION}</code>",
             f"Nusxa: <code>{INSTANCE}</code> • {_mode_text()}",
             f"Baza: <b>{_db_line()}</b>",
             f"Mini app: <code>{WEBAPP_URL}</code> • adminlar: <code>{', '.join(map(str, config.ADMIN_IDS)) or '-'}</code>"]
    return "\n".join(lines + warnings())


def _ago(seconds: int) -> str:
    d, h, m = seconds // 86400, seconds % 86400 // 3600, seconds % 3600 // 60
    return (f"{d} kun " if d else "") + (f"{h} soat " if h or d else "") + f"{m} daqiqa"


async def _people(ids) -> str:
    out = []
    for uid in ids:
        u = await db.get_user(uid) or {}
        name = html.escape(u.get("full_name") or f"ID {uid}", quote=False)
        out.append(f"{name} (@{html.escape(u['username'], quote=False)})" if u.get("username") else name)
    return ", ".join(out) or "—"


async def human_status(bot=None, technical: bool = False) -> str:
    """Tizim holati - oddiy tilda: ishlayaptimi, tezligi, qaysi server / baza / AI, kimlar boshqaradi."""
    ping_ms = await db.ping() * 1000
    speed = "yaxshi" if ping_ms < 400 else ("o'rtacha" if ping_ms < 1200 else "sekin ⚠️")
    up = int(time.time() - STARTED)
    lines = ["🩺 <b>Tizim holati</b>", "━━━━━━━━━━━━━━━━",
             f"✅ Bot ishlayapti — {_ago(up)}dan beri (oxirgi ishga tushish / deploydan)",
             f"⚡ Baza javob tezligi: <b>{ping_ms:.0f} ms</b> — {speed}"]
    if config.BOT_MODE == "webhook" and bot is not None:
        try:
            info = await bot.get_webhook_info()
            queue = info.pending_update_count
            lines.append(f"📨 Javob kutayotgan xabarlar (webhook): <b>{queue}</b>" + (" — hammasi o'z vaqtida" if queue < 5 else " ⚠️"))
            if info.url != WEBHOOK_URL:
                lines.append("⚠️ Telegram xabarlarni boshqa manzilga yuboryapti — boshqa joyda eski bot ishlayapti")
            elif info.last_error_message and info.last_error_date and time.time() - info.last_error_date.timestamp() < 600:
                lines.append(f"⚠️ Oxirgi xato: {html.escape(info.last_error_message)}")
        except Exception:
            pass
    elif config.BOT_MODE != "webhook":
        lines.append(f"📨 Xabarlarni olish usuli: {_mode_text()}")
    on_render = bool(os.getenv("RENDER"))
    server = f"Render — «{html.escape(SERVICE)}»" if on_render else f"lokal kompyuter ({html.escape(SERVICE)})"
    if config.TURSO_URL:
        base = "Turso (bulutda) — ma'lumotlar deploy va qayta ishga tushishda <b>saqlanadi</b>"
    else:
        base = "SQLite fayl — " + ("⚠️ Render'da diskda bo'lmasa, deployda o'chadi" if on_render else "lokal")
    groq, claude = bool(config.GROQ_API_KEY), bool(config.ANTHROPIC_API_KEY)
    if config.GRADER_PROVIDER == "claude":
        ai = f"Claude ({html.escape(config.CLAUDE_MODEL)})" + (" • zaxira: Groq" if groq else "")
    else:
        ai = f"Groq ({html.escape(config.GRADER_MODEL)})" + (
            f" • zaxira: Claude ({html.escape(config.CLAUDE_MODEL)})" if claude else " • zaxira: yo'q")
    admins = await db.list_admins()
    lines += ["━━━━━━━━━━━━━━━━",
              f"🖥 Server: {server} • versiya <code>{VERSION}</code>",
              f"🗄 Baza: {base}",
              f"🤖 AI baholash: {ai}",
              "✍️ Yazma: " + (f"Claude ({html.escape(config.CLAUDE_MODEL)}) + xatolarni qayta tekshirish"
                             if config.WRITING_GRADER == "claude" and claude else "Groq (asosiy xizmat)"),
              "🎤 Ovozni matnga o'girish: Groq Whisper",
              f"⏱ Bir vaqtda AI so'rovlari: {config.GRADER_CONCURRENCY}",
              f"👑 Bosslar: {await _people(config.ADMIN_IDS)}",
              f"👮 Adminlar: <b>{len(admins)}</b> ta"]
    problems = warnings()
    lines += ["━━━━━━━━━━━━━━━━"] + (problems or ["👍 Muammo topilmadi — hammasi joyida."])
    if technical:
        lines.append(f"\n<i>Texnik: nusxa <code>{html.escape(INSTANCE)}</code> • mini app <code>{WEBAPP_URL}</code></i>")
    return "\n".join(lines)


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
            self._seen[user.id] = time.time()
            # fonda: javob bazaga yozilishini kutmaydi (har yangi foydalanuvchida ~0.2 s tejaladi)
            _saving[user.id] = asyncio.create_task(self._save(user))
        return await handler(event, data)

    async def _save(self, user):
        try:
            await db.save_user(user.id, user.full_name, user.username)
        except Exception:
            self._seen.pop(user.id, None)  # keyingi xabarda qayta urinadi
            log.exception("Foydalanuvchini saqlab bo'lmadi (%s)", user.id)
        finally:
            _saving.pop(user.id, None)


_saving: dict[int, asyncio.Task] = {}  # hali bazaga yozilayotgan foydalanuvchilar


async def user_saved(user_id: int):
    """Foydalanuvchi qatori bazaga yozilib bo'lishini kutish (uni o'zgartiradigan amaldan oldin)."""
    task = _saving.get(user_id)
    if task:
        await asyncio.shield(task)


# Pastki menyu (reply-klaviatura) Telegram'da o'zi yangilanmaydi - faqat bot yangisini yuborganda.
# Menyuga tugma qo'shilganda shu son oshiriladi: har bir foydalanuvchiga yangi menyu BIR MARTA yuboriladi.
MENU_VERSION = 5
MENU_NEWS = "🔄 Menyu yangilandi: «🎙 Konuşma» va «✍️ Yazma» — mockni tanlang, Full mock yoki istalgan qismni alohida ishlang."


class MenuRefreshMiddleware(BaseMiddleware):
    def __init__(self):
        self._fresh: set[int] = set()

    async def __call__(self, handler, event: Message, data):
        user = event.from_user
        if not user or user.is_bot or event.chat.type != "private" or user.id in self._fresh:
            return await handler(event, data)
        try:
            if await db.get_menu_version(user.id) < MENU_VERSION:
                if not (event.text or "").startswith("/start"):  # /start yangi menyuni o'zi yuboradi
                    from bot import main_keyboard
                    await event.answer(MENU_NEWS, reply_markup=main_keyboard(user.id))
                await user_saved(user.id)  # yangi foydalanuvchi qatori yozilib bo'lsin - aks holda UPDATE hech narsa qilmaydi
                await db.set_menu_version(user.id, MENU_VERSION)
            self._fresh.add(user.id)
        except Exception:
            log.warning("Menyuni yangilab bo'lmadi (%s)", user.id, exc_info=True)
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
    st = await db.get_stats()
    mocks = st["mocks"]
    await message.answer(
        await human_status(message.bot, technical=True)
        + f"\n\n👥 O'quvchilar: <b>{st['users']}</b> (bugun +{st['new_today']}) • 📚 Mocklar: <b>{mocks}</b>\n"
        f"🎯 Bugungi tekshiruvlar: <b>{st['today']}</b>\n\n"
        "<i>/status ni bir necha marta yuboring: «nusxa» har safar bir xil bo'lishi kerak. Har xil bo'lsa — "
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
    """Adminlarga faqat muammo bo'lsa yoziladi (har deployda "ishga tushdi" xabari yuborilmaydi)."""
    problems = warnings()
    for w in problems:
        log.error(re.sub(r"<[^>]+>", "", w))
    log.info("Ishga tushdi: %s", re.sub(r"<[^>]+>", "", instance_line()))
    if problems:
        await _notify_admins(bot, "⚠️ <b>Sozlamada muammo</b>\n" + "\n".join(problems) + "\nTekshirish: /status")


# ---------------------------------------------------------------- yagona faol nusxa

class _Leader:
    """Telegram'dan xabarlarni faqat bitta nusxa oladi (bazadagi bot_lock orqali).
    Yangi ishga tushgan nusxa boshqaruvni oladi (deploy), eskisi o'zi to'xtaydi - ikki bot
    bir-biri bilan urishib (TelegramConflictError) qotib qolmaydi va javoblar ikki marta kelmaydi."""
    BEAT = 15     # har necha soniyada tekshiriladi
    LEASE = 60    # shuncha vaqt yurak urishi bo'lmasa - nusxa o'lgan hisoblanadi

    def __init__(self):
        self.active = False
        self.stepped_down = False
        self.regained = asyncio.Event()


leader = _Leader()


async def take_leadership():
    await db.lock_take(INSTANCE, SERVICE)
    leader.active = True


async def leader_loop(bot, dp):
    warned_for = None
    while True:
        await asyncio.sleep(leader.BEAT)
        try:
            row = await db.lock_get()
            if leader.active:
                if row and row["instance"] != INSTANCE:
                    leader.active = False
                    leader.stepped_down = True
                    leader.regained.clear()
                    log.warning("Boshqa nusxa (%s / %s) faol bo'ldi - bu nusxa kutish rejimiga o'tdi", row["service"], row["instance"])
                    if row["service"] != SERVICE and warned_for != row["service"]:
                        warned_for = row["service"]
                        await _notify_admins(bot, (
                            "⚠️ <b>Bot ikkita servisda ishga tushirilgan!</b>\n"
                            f"Faol: <b>{row['service']}</b>. Kutish rejimiga o'tdi: <b>{SERVICE}</b>.\n"
                            "Ikkalasi kerak emas — Render'da keraksizini o'chiring (Settings → Delete / Suspend)."
                        ))
                    try:
                        await dp.stop_polling()
                    except RuntimeError:
                        pass
                else:
                    await db.lock_beat(INSTANCE)
            elif not row or (row.get("age") or 0) > leader.LEASE:
                # faol nusxa to'xtagan - boshqaruvni olamiz
                await db.lock_take(INSTANCE, SERVICE)
                leader.active = True
                leader.regained.set()
                log.info("Faol nusxa javob bermadi - bu nusxa boshqaruvni oldi")
        except Exception:
            log.warning("Yetakchilikni tekshirib bo'lmadi", exc_info=True)


async def set_webhook(bot, dp):
    await bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET, allowed_updates=dp.resolve_used_update_types(),
                          drop_pending_updates=False, max_connections=40)
    log.info("Webhook o'rnatildi: %s/tg/…", WEBAPP_URL)


async def webhook_watch(bot, dp):
    """Har daqiqada: webhook bizning manzilda turibdimi. Boshqa joyda eski (polling) bot ishga tushib
    webhookni o'chirib qo'ysa - qayta o'rnatamiz va adminlarga bir marta aytamiz."""
    warned = False
    while True:
        await asyncio.sleep(WEBHOOK_CHECK)
        try:
            info = await bot.get_webhook_info()
            if info.url == WEBHOOK_URL:
                continue
            log.warning("Webhook o'zgartirilgan (%r) - qayta o'rnatilmoqda", info.url)
            await set_webhook(bot, dp)
            if not warned:
                warned = True
                await _notify_admins(bot, (
                    "⚠️ <b>Shu token bilan boshqa joyda ham bot ishlayapti</b> (eski servis, Background Worker yoki "
                    "kompyuterdagi nusxa) va u botni o'ziga tortib oldi. Bot qayta tiklandi, lekin o'sha nusxani "
                    f"o'chiring.\nBu nusxa — {instance_line()}"
                ))
        except Exception:
            log.warning("Webhookni tekshirib bo'lmadi", exc_info=True)


async def release_leadership():
    if leader.active:
        try:
            await db.lock_release(INSTANCE)
        except Exception:
            pass


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
        try:
            n = await db.purge_expired_personal()  # o'quvchilarning 2 soatlik shaxsiy testlari
            if n:
                log.info("Muddati o'tgan %s ta shaxsiy test o'chirildi", n)
        except Exception:
            log.warning("Shaxsiy testlarni tozalab bo'lmadi", exc_info=True)


async def drain_background(timeout: float = 25):
    """Bot to'xtayotganda fonda ketayotgan yazma tekshiruvlarini biroz kutadi."""
    from bot import _background
    if _background:
        await asyncio.wait(list(_background), timeout=timeout)


def setup(dp: Dispatcher, bot):
    """main.py dan: barcha routerlardan KEYIN chaqiriladi (fallback oxirida turishi kerak)."""
    dp.update.outer_middleware(UserTouchMiddleware())
    dp.message.outer_middleware(MenuRefreshMiddleware())
    dp.errors.register(on_error)
    dp.include_router(fallback)
    logging.getLogger("aiogram.dispatcher").addHandler(ConflictWatcher(bot))
