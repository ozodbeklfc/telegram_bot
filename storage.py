"""
Хранилище состояний бота в Postgres.

Зачем: aiogram по умолчанию держит состояние в оперативной памяти
(MemoryStorage). При каждом деплое на Railway процесс перезапускается, и все,
кто в этот момент заполняли форму, теряют прогресс: кнопки перестают
работать, потому что бот больше не помнит, на каком шаге человек находился.

Здесь состояние лежит в таблице, поэтому перезапуск бота ничего не рвёт —
агент нажимает кнопку и продолжает с того же места.

Таблица создаётся автоматически при первом запуске.
"""

import json
import logging
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

logger = logging.getLogger(__name__)


class PostgresStorage(BaseStorage):
    """Состояния и данные FSM в таблице bot_state."""

    def __init__(self, pool_getter):
        # Пул берём той же функцией, что и остальной код: одно подключение
        # к базе на всё приложение
        self._pool_getter = pool_getter
        self._ready = False

    async def _pool(self):
        pool = await self._pool_getter()
        if not self._ready:
            async with pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key        TEXT PRIMARY KEY,
                        state      TEXT,
                        data       JSONB NOT NULL DEFAULT '{}'::jsonb,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
            self._ready = True
        return pool

    @staticmethod
    def _key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}"

    async def set_state(self, key: StorageKey, state: Optional[State] = None) -> None:
        value = state.state if isinstance(state, State) else state
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_state (key, state) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE
                    SET state = EXCLUDED.state, updated_at = now()
            """, self._key(key), value)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT state FROM bot_state WHERE key = $1", self._key(key))

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        pool = await self._pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO bot_state (key, data) VALUES ($1, $2::jsonb)
                ON CONFLICT (key) DO UPDATE
                    SET data = EXCLUDED.data, updated_at = now()
            """, self._key(key), json.dumps(data or {}, ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        pool = await self._pool()
        async with pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT data FROM bot_state WHERE key = $1", self._key(key))
        if not raw:
            return {}
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    async def update_data(self, key: StorageKey, data: Dict[str, Any]) -> Dict[str, Any]:
        current = await self.get_data(key)
        current.update(data or {})
        await self.set_data(key, current)
        return current

    async def close(self) -> None:
        # Пул закрывается в api.close_pool() — здесь закрывать нечего
        pass

    async def wait_closed(self) -> None:
        pass
