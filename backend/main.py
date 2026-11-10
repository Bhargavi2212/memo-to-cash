"""FastAPI app (Render api service: `uvicorn backend.main:app`).

Every route lives under /api, including the docs, because the web service's proxy
forwards only /api/* to this service.
"""

import logging

from fastapi import FastAPI

from backend.core import logging as token_logging
from backend.routes import config, demo, health, statement

# PayPal/LLM code logs method, path, status, ms and debug_id — never bodies with emails or tokens.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
token_logging.install()  # statement tokens never reach the access log (P2 D14)

app = FastAPI(
    title="Memo-to-Cash",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
app.include_router(health.router)
app.include_router(demo.router)
app.include_router(statement.router)
app.include_router(config.router)
