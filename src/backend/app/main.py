"""FastAPI construction (SHARED file) - the single application of the system.

The API is a transport boundary: it authenticates, validates and dispatches routes. It
contains no payroll or eligibility rules.

`create_app()` only registers routes and middleware (no database access), so importing the
module is cheap. The lifespan composes the runtime in this order:

1. Business services (one engine), then `BusinessApi` (auth, HTTP settings).
2. The AI runtime (conversation service, LangGraph runner, turn worker) over the shared ports.
3. A read-only check that both schemas are at their migration head (never migrates).
4. When `STRAUSS_WORKER_IN_PROCESS=true`: the in-process turn worker (decision W).

Shutdown releases everything in reverse order and is safe after a partial startup.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.application.ports.llm import LlmPort
from app.api.business.dependencies import BusinessApi, HttpConfig
from app.api.business.installer import include_business_routers
from app.api.routers import conversations, health
from app.bootstrap import (
    Container,
    ReadinessSources,
    build_ai_runtime,
    build_auth_service,
    build_business_services,
    build_container,
    build_readiness_probe,
    verify_schemas,
)
from app.settings import Settings, get_settings

API_PREFIX = "/api/v1"
CORS_HEADERS = ["Authorization", "Content-Type", "X-CSRF-Protection", "X-Request-ID"]

logger = logging.getLogger("app.main")


def _http_config(settings: Settings) -> HttpConfig:
    return HttpConfig(
        cookie_secure=settings.auth_cookie_secure,
        allowed_origins=tuple(settings.auth_allowed_origins),
        cookie_samesite=settings.auth_cookie_samesite,
    )


def _lifespan(
    settings: Settings, llm: LlmPort | None, ai_database_url: str | None
) -> Callable[[FastAPI], Any]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            services = build_business_services(settings)
            stack.push_async_callback(services.engine.dispose)
            app.state.business_api = BusinessApi(
                auth=build_auth_service(settings, services),
                tools=services.tools,
                cases=services.cases,
                profiles=services.profiles,
                http=_http_config(settings),
                representatives=services.representative_answers,
            )
            runtime = await build_ai_runtime(services, llm=llm, ai_database_url=ai_database_url)
            stack.push_async_callback(runtime.aclose)
            await verify_schemas(services, runtime)
            app.state.business_services = services
            app.state.ai_runtime = runtime
            app.state.ai_conversations = runtime.conversations
            sources = ReadinessSources(services, runtime, settings.worker_in_process)
            if settings.worker_in_process:
                runtime.in_process.start()
                # `runtime.aclose` (registered above) stops the worker first.
            app.state.readiness_probe = build_readiness_probe(sources)
            logger.info("startup_complete worker_in_process=%s", settings.worker_in_process)
            try:
                yield
            finally:
                for name in ("readiness_probe", "ai_conversations", "business_api"):
                    if hasattr(app.state, name):
                        delattr(app.state, name)
                logger.info("shutdown_started")

    return lifespan


def create_app(
    settings: Settings | None = None,
    *,
    llm: LlmPort | None = None,
    ai_database_url: str | None = None,
) -> FastAPI:
    """The shared application. `llm` and `ai_database_url` are injection points for tests
    (a scripted model, a test database); production reads both from the environment."""
    settings = settings or get_settings()
    container: Container = build_container(settings)
    app = FastAPI(
        title="Strauss Vehicle Advisor API",
        version="0.1.0",
        lifespan=_lifespan(settings, llm, ai_database_url),
    )
    app.state.container = container
    if settings.auth_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.auth_allowed_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
            allow_headers=CORS_HEADERS,
            expose_headers=["X-Request-ID"],
        )
    app.include_router(health.router, prefix=API_PREFIX)
    include_business_routers(app)
    app.include_router(conversations.router, prefix=API_PREFIX)
    return app


app = create_app()
