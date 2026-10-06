"""FSM holatini (ko'p bosqichli amallar: savol qo'shish, premium berish, yazma yozish...) bazada saqlash.

Avval holat faqat xotirada edi (MemoryStorage): Render qayta ishga tushganda (deploy, uyqudan uyg'onish)
u o'chib ketar va «⭐ 45 s», «✅ Shunday yuborish», «30 kun» kabi tugmalar hech narsa qilmay qolardi.
Endi holat bazada (Turso yoki SQLite) turadi; tezlik uchun xotirada ham nusxasi saqlanadi."""
import json
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

import db


def _key(k: StorageKey) -> str:
    return f"{k.bot_id}:{k.chat_id}:{k.user_id}:{k.thread_id or 0}:{k.business_connection_id or ''}:{k.destiny}"


class DbStorage(BaseStorage):
    def __init__(self):
        self._cache: dict[str, tuple[str | None, dict]] = {}

    async def _load(self, key: StorageKey) -> tuple[str, tuple[str | None, dict]]:
        k = _key(key)
        if k not in self._cache:
            row = await db.fsm_get(k)
            data = {}
            if row and row.get("data"):
                try:
                    data = json.loads(row["data"])
                except ValueError:
                    data = {}
            self._cache[k] = (row.get("state") if row else None, data if isinstance(data, dict) else {})
        return k, self._cache[k]

    async def _save(self, k: str, state: str | None, data: dict):
        self._cache[k] = (state, data)
        await db.fsm_set(k, state, json.dumps(data, ensure_ascii=False, default=str) if data else "")

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
