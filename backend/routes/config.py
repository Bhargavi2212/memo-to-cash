"""GET /api/config — public settings the browser needs at runtime (P2 D12).

The PayPal client id is public by design (it's in every page that loads the JS SDK); serving
it here means no NEXT_PUBLIC_* build-time variable (same reason as the P0 runtime proxy).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.core.config import Settings, get_settings

router = APIRouter(prefix="/api")


class PublicConfig(BaseModel):
    paypal_client_id: str | None
    demo_mode: bool


@router.get("/config", response_model=PublicConfig)
def public_config(settings: Annotated[Settings, Depends(get_settings)]) -> PublicConfig:
    return PublicConfig(paypal_client_id=settings.paypal_client_id, demo_mode=settings.demo_mode)
