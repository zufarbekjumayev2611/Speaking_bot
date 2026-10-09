"""Bot + mini app serverini BITTA jarayonda ishga tushiradi (Render Web Service).

Render Web Service'da bot webhook orqali ishlaydi: Telegram har bir xabarni shu serverga o'zi yuboradi
(so'rab turish yo'q - tez, ikki nusxa bir-biri bilan urishib qotib qolmaydi). Background Worker'da
(BOT_MODE=off standart) bot umuman yurgizilmaydi - u kerak emas, uni o'chirish mumkin.
Kompyuterda (Render'dan tashqarida) bot eskicha polling bilan ishlaydi."""
import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import ClientTimeout
from aiohttp import web

import db
import ops
from bot import router
from broadcast import reminder_loop, router as broadcast_router
from premium import BlockMiddleware, router as premium_router
from config import BOT_MODE, BOT_TOKEN, PORT, WEBAPP_URL
from fsm_storage import DbStorage
from netclient import close_session, get_session
from web import create_app

logging.basicConfig(level=logging.INFO)


def build_dispatcher(bot: Bot) -> Dispatcher:
    # Holat bazada saqlanadi (qayta ishga tushganda tugmalar ishlashda davom etadi);
    # bitta foydalanuvchining xabarlari navbat bilan ishlanadi (tez ikki marta bosish chalkashtirmaydi).
    dp = Dispatcher(storage=DbStorage(), events_isolation=SimpleEventIsolation())
    dp.include_router(router)
    dp.include_router(premium_router)
    dp.include_router(broadcast_router)
    dp.update.outer_middleware(BlockMiddleware())
    ops.setup(dp, bot)  # oxirida: tanilmagan tugma/xabar, xatolar, /status
    return dp


async def keep_alive():
    """Render bepul rejasi 15 daqiqa jimlikdan keyin uxlaydi - o'zini ping qilib turadi."""
    while True:
        await asyncio.sleep(600)
        try:
            async with get_session().get(f"{WEBAPP_URL}/health", timeout=ClientTimeout(total=30)) as r:
                logging.info("Keep-alive: %s", r.status)
        except Exception:
            logging.warning("Keep-alive muvaffaqiyatsiz")


async def _stop(dp: Dispatcher):
    try:
        await dp.stop_polling()
    except RuntimeError:
        pass  # polling ishlamayapti (kutish rejimi)


async def main():
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    dp_ref: list[Dispatcher] = []

    def on_signal():  # Render to'xtatganda (deploy / restart) - qaysi rejimda bo'lsa ham chiqamiz
        shutdown.set()
        if dp_ref and BOT_MODE == "polling":
            asyncio.ensure_future(_stop(dp_ref[0]))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_signal)
        except (NotImplementedError, RuntimeError):
            pass

    if BOT_MODE == "off":
        logging.warning("BOT_MODE=off: bu servis botni yurgizmaydi (bot Web Service'da webhook orqali ishlaydi). "
                        "Bu servis kerak emas - Render'da uni o'chirib qo'yishingiz mumkin.")
        await shutdown.wait()
        return

    await db.init_db()
    logging.info("Ma'lumotlar bazasi: %s", db.backend_name())
    await db.load_admins()
    await db.load_blocked()
    bot = Bot(token=BOT_TOKEN)
    dp = build_dispatcher(bot)
    dp_ref.append(dp)

    app = create_app(bot)
    hook = None
    if BOT_MODE == "webhook":
        hook = SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=ops.WEBHOOK_SECRET)
        hook.register(app, path=ops.WEBHOOK_PATH)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info("Mini app server: port %s, bot rejimi: %s", PORT, BOT_MODE)

    asyncio.create_task(keep_alive())
    asyncio.create_task(reminder_loop(bot))
    try:
        if BOT_MODE == "webhook":
            await ops.set_webhook(bot, dp)
            ops.leader.active = True
            await ops.startup(bot)
            asyncio.create_task(ops.refresh_loop())
            asyncio.create_task(ops.webhook_watch(bot, dp))
            await shutdown.wait()
            # webhook o'chirilmaydi: yangi nusxa (deploy) xabarlarni shu manzilda qabul qilishda davom etadi
        else:
            await bot.delete_webhook(drop_pending_updates=False)
            await ops.take_leadership()   # yangi nusxa boshqaruvni oladi; eski nusxa 15 soniyada o'zi to'xtaydi
            await ops.startup(bot)
            asyncio.create_task(ops.refresh_loop())
            asyncio.create_task(ops.leader_loop(bot, dp))
            while not shutdown.is_set():
                await dp.start_polling(bot, handle_signals=False)
                if shutdown.is_set() or not ops.leader.stepped_down:
                    break
                # boshqa nusxa faol - u to'xtaguncha kutamiz (mini app serveri ishlashda davom etadi)
                waiters = [asyncio.ensure_future(ops.leader.regained.wait()), asyncio.ensure_future(shutdown.wait())]
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                for w in waiters:
                    w.cancel()
                ops.leader.stepped_down = False
    finally:
        if hook and hook._background_feed_update_tasks:  # qabul qilingan xabarlar oxirigacha ishlansin
            await asyncio.wait(list(hook._background_feed_update_tasks), timeout=15)
        await ops.drain_background()  # deploy paytida boshlangan yazma tekshiruvlari tugasin
        await ops.release_leadership()
        await runner.cleanup()
        await bot.session.close()
        await close_session()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
