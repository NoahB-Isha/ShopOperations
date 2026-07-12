from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .admin.system_router import router as admin_system_router
from .admin.users_router import router as admin_users_router
from .auth.router import router as auth_router
from .catalog.router import router as catalog_router
from .centers.router import router as centers_router
from .config import get_settings
from .health import router as health_router
from .odoo.errors import OdooWriteError
from .odoo.writer import WriterValidationError
from .orders.router import router as orders_router
from .restock.router import router as restock_router
from .transfers.router import adjustments_router
from .transfers.router import router as transfers_router


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    app = FastAPI(
        title="Isha Life Shop Ops API",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

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
        restock_router,
        transfers_router,
        adjustments_router,
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
        return {"app": "Isha Life Shop Ops", "version": __version__, "docs": "/api/docs"}

    return app


app = create_app()
