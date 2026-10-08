"""Bot + mini app serverini BITTA jarayonda ishga tushiradi (Render Web Service).
MUHIM: shu botni faqat BITTA joyda ishga tushiring - aks holda TelegramConflictError
(xabarlar ikki nusxa orasida bo'linib, bot "bir safar bor, bir safar yo'q" deb javob beradi).
Ikkinchi nusxa paydo bo'lsa, bot adminlarga o'zi xabar beradi (ops.py)."""
import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiohttp import ClientTimeout
from aiohttp import web

import db
import ops
from bot import router
from broadcast import reminder_loop, router as broadcast_router
from premium import BlockMiddleware, router as premium_router
from config import BOT_TOKEN, PORT, WEBAPP_URL
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
    await db.init_db()
    logging.info("Ma'lumotlar bazasi: %s", db.backend_name())
    await db.load_admins()
    await db.load_blocked()
    bot = Bot(token=BOT_TOKEN)
    dp = build_dispatcher(bot)

    runner = web.AppRunner(create_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info("Mini app server: port %s", PORT)

    asyncio.create_task(keep_alive())
    asyncio.create_task(reminder_loop(bot))
    await bot.delete_webhook(drop_pending_updates=False)
    await ops.take_leadership()   # yangi nusxa boshqaruvni oladi; eski nusxa 15 soniyada o'zi to'xtaydi
    await ops.startup(bot)
    asyncio.create_task(ops.refresh_loop())
    asyncio.create_task(ops.leader_loop(bot, dp))
    shutdown = asyncio.Event()

    def on_signal():  # Render to'xtatganda (deploy / restart) - faol bo'lsa ham, kutishda bo'lsa ham chiqamiz
        shutdown.set()
        asyncio.ensure_future(_stop(dp))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, on_signal)
        except (NotImplementedError, RuntimeError):
            pass
    try:
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
        await ops.drain_background()  # deploy paytida boshlangan yazma tekshiruvlari tugasin
        await ops.release_leadership()
        await close_session()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
