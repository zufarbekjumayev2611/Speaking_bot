"""Bot + mini app serverini BITTA jarayonda ishga tushiradi (Render Web Service).
MUHIM: shu botni faqat BITTA joyda ishga tushiring - aks holda TelegramConflictError."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiohttp import ClientTimeout
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

import db
from bot import router
from broadcast import reminder_loop, router as broadcast_router
from premium import BlockMiddleware, router as premium_router
from config import BOT_TOKEN, PORT, WEBAPP_URL
from netclient import close_session, get_session
from web import create_app

logging.basicConfig(level=logging.INFO)


async def keep_alive():
    """Render bepul rejasi 15 daqiqa jimlikdan keyin uxlaydi - o'zini ping qilib turadi."""
    while True:
        await asyncio.sleep(600)
        try:
            async with get_session().get(f"{WEBAPP_URL}/health", timeout=ClientTimeout(total=30)) as r:
                logging.info("Keep-alive: %s", r.status)
        except Exception:
            logging.warning("Keep-alive muvaffaqiyatsiz")


async def main():
    await db.init_db()
    await db.load_admins()
    await db.load_blocked()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.include_router(premium_router)
    dp.include_router(broadcast_router)
    dp.update.outer_middleware(BlockMiddleware())

    runner = web.AppRunner(create_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info("Mini app server: port %s", PORT)

    asyncio.create_task(keep_alive())
    asyncio.create_task(reminder_loop(bot))
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await dp.start_polling(bot)
    finally:
        await close_session()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
