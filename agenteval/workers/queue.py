from typing import Protocol

from redis.asyncio import Redis


class RunQueue(Protocol):
    async def enqueue(self, run_id: str) -> None: ...


class RedisRunQueue:
    def __init__(self, redis: Redis, name: str):
        self._redis = redis
        self._name = name

    async def enqueue(self, run_id: str) -> None:
        await self._redis.rpush(self._name, run_id)

    async def dequeue(self, wait_s: int = 5) -> str | None:
        item = await self._redis.blpop([self._name], timeout=wait_s)
        if not item:
            return None
        value = item[1]
        return value.decode() if isinstance(value, bytes) else value
