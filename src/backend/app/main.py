"""FastAPI construction.

SHARED file (Business + AI developers) - provisional skeleton from Step 0.
The API is a transport boundary: it authenticates, validates and dispatches routes.
It contains no payroll or eligibility rules.
"""

from fastapi import FastAPI

from app.api.routers import health
from app.bootstrap import Container, build_container
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    container: Container = build_container(settings or get_settings())
    app = FastAPI(title="Strauss Vehicle Advisor API", version="0.1.0")
    app.state.container = container
    app.include_router(health.router, prefix="/api/v1")
    return app


app = create_app()
