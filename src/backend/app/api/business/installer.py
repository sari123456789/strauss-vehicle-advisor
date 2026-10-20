"""Install the Business API on a FastAPI application - an isolated, integrable component.

`install_business_api(app, api)` adds only the Business routers under `/api/v1` and stores
the injected `BusinessApi`; it registers no application-wide handlers or middleware, so the
shared `app.main` and the AI-owned routes are unaffected. Integration into `app.main` /
`app.bootstrap` is a coordinated change (see docs/business-decisions.md §18).

`business_openapi()` builds the OpenAPI document of the Business API alone (no services
needed), for export and the drift check.
"""

from typing import Any

from fastapi import FastAPI

from app.api.business.dependencies import BusinessApi
from app.api.business.routers import auth, cases, profile

API_PREFIX = "/api/v1"
OPENAPI_TITLE = "Strauss Vehicle Advisor - Business API"
OPENAPI_VERSION = "0.1.0"


def include_business_routers(app: FastAPI) -> None:
    """Routes only; the `BusinessApi` is set later (`app.state.business_api`, lifespan)."""
    for module in (auth, profile, cases):
        app.include_router(module.router, prefix=API_PREFIX)


def install_business_api(app: FastAPI, api: BusinessApi) -> None:
    app.state.business_api = api
    include_business_routers(app)


def business_openapi() -> dict[str, Any]:
    app = FastAPI(title=OPENAPI_TITLE, version=OPENAPI_VERSION)
    for module in (auth, profile, cases):
        app.include_router(module.router, prefix=API_PREFIX)
    return app.openapi()
