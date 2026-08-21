from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .admin.system_router import router as admin_system_router
from .admin.users_router import router as admin_users_router
from .auth.router import router as auth_router
from .availability.bot import router as bot_router
from .availability.router import router as availability_router
from .catalog.router import router as catalog_router
from .center_orders.router import router as center_orders_router
from .centers.router import router as centers_router
from .config import get_settings
from .counting.router import router as counting_router
from .floor_requests.router import router as floor_requests_router
from .health import router as health_router
from .notices.router import router as notices_router
from .odoo.errors import OdooWriteError
from .odoo.writer import WriterValidationError
from .oos.router import router as oos_router
from .ordering.router import router as ordering_router
from .orders.router import router as orders_router
from .reporting.router import router as reports_router
from .restock.router import router as restock_router
from .security_headers import SecurityHeadersMiddleware
from .timemachine.router import router as timemachine_router
from .transfers.router import adjustments_router
from .transfers.router import router as transfers_router


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    app = FastAPI(
        title="Isha Life Shop Ops API",
        version=__version__,
        # Dev only. In production the interactive docs hand an anonymous caller
        # the whole surface map (every path, payload shape and admin operation),
        # which is reconnaissance we don't need to give away. `make openapi`
        # exports the schema straight off the app object, so the committed
        # contract in docs/api/openapi.json is unaffected.
        docs_url="/api/docs" if settings.is_dev_env else None,
        openapi_url="/api/openapi.json" if settings.is_dev_env else None,
    )

    # HSTS only off-dev: on plain-http localhost it would pin the browser to
    # https for a year.
    app.add_middleware(SecurityHeadersMiddleware, hsts=not settings.is_dev_env)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        health_router,
        auth_router,
        catalog_router,
        centers_router,
        orders_router,
        ordering_router,
        center_orders_router,
        restock_router,
        floor_requests_router,
        oos_router,
        notices_router,
        transfers_router,
        adjustments_router,
        counting_router,
        availability_router,
        bot_router,
        timemachine_router,
        reports_router,
        admin_users_router,
        admin_system_router,
    ):
        app.include_router(router, prefix="/api/v1")

    @app.exception_handler(WriterValidationError)
    async def writer_validation(_req: Request, exc: WriterValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(OdooWriteError)
    async def odoo_write_error(_req: Request, exc: OdooWriteError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        body: dict = {"app": "Isha Life Shop Ops", "version": __version__}
        # Only advertise the docs where they actually exist (see docs_url above).
        if settings.is_dev_env:
            body["docs"] = "/api/docs"
        return body

    return app


app = create_app()
