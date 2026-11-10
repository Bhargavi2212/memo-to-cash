"""Create one demo copy on the database in your .env (Neon `dev`) with REAL sandbox invoices.

    python -m seed.demo_seed

Same code path as POST /api/demo/start (importer → seeded proposal → PayPal mirror), but
no cookie and no rate limit. Refuses when APP_ENV=prod. Prints the statement token, the
buyer id and how long it took — never secrets.
"""

import asyncio
import logging
import sys

from backend.core.config import settings
from backend.db.session import SessionLocal
from backend.paypal.client import PayPal
from backend.services import demo


async def main() -> int:
    if settings.app_env == "prod":
        print("Refusing: APP_ENV=prod. Demo copies on production come from /api/demo/start.")
        return 2
    try:
        started = await demo.start_demo(settings, PayPal(settings), SessionLocal, ip="seed-cli")
    except demo.DemoError as e:
        print(f"failed: {e.status} {e.code} — {e.message}")
        return 1
    print(f"statement_token={started.statement_token}")
    print(f"buyer_id={started.buyer_id} ms={started.ms}")
    print(f"statement: {settings.app_base_url}/s/{started.statement_token}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(main()))
