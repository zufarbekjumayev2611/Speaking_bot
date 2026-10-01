"""Bot + mini app serverini BITTA jarayonda ishga tushiradi (Render Web Service).
MUHIM: shu botni faqat BITTA joyda ishga tushiring - aks holda TelegramConflictError."""
import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

import db
from bot import router
from config import BOT_TOKEN, PORT, WEBAPP_URL
from web import create_app

logging.basicConfig(level=logging.INFO)


async def keep_alive():
    """Render bepul rejasi 15 daqiqa jimlikdan keyin uxlaydi - o'zini ping qilib turadi."""
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(600)
            try:
                async with session.get(f"{WEBAPP_URL}/health", timeout=aiohttp.ClientTimeout(total=30)) as r:
                    logging.info("Keep-alive: %s", r.status)
            except Exception:
                logging.warning("Keep-alive muvaffaqiyatsiz")


async def main():
    await db.init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    runner = web.AppRunner(create_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logging.info("Mini app server: port %s", PORT)

    asyncio.create_task(keep_alive())
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
