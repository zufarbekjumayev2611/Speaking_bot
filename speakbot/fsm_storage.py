"""FSM holatini (ko'p bosqichli amallar: savol qo'shish, premium berish, yazma yozish...) bazada saqlash.

Avval holat faqat xotirada edi (MemoryStorage): Render qayta ishga tushganda (deploy, uyqudan uyg'onish)
u o'chib ketar va «⭐ 45 s», «✅ Shunday yuborish», «30 kun» kabi tugmalar hech narsa qilmay qolardi.
Endi holat bazada (Turso yoki SQLite) turadi; tezlik uchun xotirada ham nusxasi saqlanadi."""
import json
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

import db


TTL = 12 * 3600  # 12 soatdan beri tegilmagan yarim qolgan amal unutiladi (keyingi xabar eski amalga ketmasin)
FRESH = 30       # xotiradagi nusxa necha soniya ishlatiladi (deploy paytida ikki nusxa bo'lsa ham chalkashmasin)


def _age(updated_at: str | None) -> float:
    try:
        dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return time.time() - dt.timestamp()
    except (TypeError, ValueError):
        return 0.0


def _key(k: StorageKey) -> str:
    return f"{k.bot_id}:{k.chat_id}:{k.user_id}:{k.thread_id or 0}:{k.business_connection_id or ''}:{k.destiny}"


class DbStorage(BaseStorage):
    def __init__(self):
        self._cache: dict[str, tuple[str | None, dict]] = {}
        self._touched: dict[str, float] = {}

    async def _load(self, key: StorageKey) -> tuple[str, tuple[str | None, dict]]:
        k = _key(key)
        if k in self._cache and time.time() - self._touched.get(k, 0) > FRESH:
            del self._cache[k]  # xotiradagi nusxa faqat qisqa vaqt ishonchli - keyin bazadan qayta o'qiladi
        if k not in self._cache:
            row = await db.fsm_get(k)
            state, data = (row.get("state"), row.get("data")) if row else (None, None)
            try:
                data = json.loads(data) if data else {}
            except ValueError:
                data = {}
            if row and _age(row.get("updated_at")) > TTL:
                state, data = None, {}
                await db.fsm_set(k, None, "")
            self._cache[k] = (state, data if isinstance(data, dict) else {})
            self._touched[k] = time.time()
        return k, self._cache[k]

    async def _save(self, k: str, state: str | None, data: dict):
        if self._cache.get(k) == (state, data):
            return  # o'zgarmagan (masalan, bo'sh holatni yana tozalash) - bazaga murojaat shart emas
        self._cache.pop(k, None)  # bazaga yozilmasa - keyingi safar bazadan qayta o'qiladi
        await db.fsm_set(k, state, json.dumps(data, ensure_ascii=False, default=str) if data else "")
        self._cache[k] = (state, data)
        self._touched[k] = time.time()

    async def set_state(self, key: StorageKey, state: State | str | None = None) -> None:
        k, (_, data) = await self._load(key)
        await self._save(k, state.state if isinstance(state, State) else state, data)

    async def get_state(self, key: StorageKey) -> str | None:
        _, (state, _) = await self._load(key)
        return state

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        k, (state, _) = await self._load(key)
        await self._save(k, state, dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        _, (_, data) = await self._load(key)
        return dict(data)

    async def close(self) -> None:
        self._cache.clear()
