from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from agenteval.apps.api.routes import dev, meta, runs
from agenteval.config import get_settings
from agenteval.dev.stage import DEV_DIR
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    # Used by the one-agent-at-a-time endpoints; tests replace these with fakes.
    app.state.stage_deps = dev.default_stage_deps
    app.state.dev_dir = DEV_DIR
    app.state.stage_running = set()
    app.include_router(runs.router)
    app.include_router(meta.router)
    app.include_router(dev.router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
