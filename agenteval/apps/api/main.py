from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from agenteval.apps.api.routes import runs
from agenteval.config import get_settings
from agenteval.storage.db import make_engine, make_sessionmaker
from agenteval.workers.queue import RedisRunQueue


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url)
    app.state.sessions = make_sessionmaker(engine)
    app.state.queue = RedisRunQueue(redis, settings.run_queue)
    yield
    await redis.aclose()
    await engine.dispose()


def create_app(use_lifespan: bool = True) -> FastAPI:
    app = FastAPI(title="AgentEval", version="0.1.0", lifespan=lifespan if use_lifespan else None)
    app.include_router(runs.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
