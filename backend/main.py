"""FastAPI app (Render api service: `uvicorn backend.main:app`).

Every route lives under /api, including the docs, because the web service's proxy
forwards only /api/* to this service.
"""

import logging

from fastapi import FastAPI

from backend.routes import health

# PayPal/LLM code logs method, path, status, ms and debug_id — never bodies with emails or tokens.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Memo-to-Cash",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
app.include_router(health.router)
