"""FastAPI app (Render api service: `uvicorn backend.main:app`).

Every route lives under /api, including the docs, because the web service's proxy
forwards only /api/* to this service.
"""

from fastapi import FastAPI

from backend.routes import health

app = FastAPI(
    title="Memo-to-Cash",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)
app.include_router(health.router)
