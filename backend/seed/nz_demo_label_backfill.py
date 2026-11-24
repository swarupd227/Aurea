"""Mark the New Zealand demo firm as a demonstration, so the shell says its clients are invented (B-250).

Until now only the Indian firm carried the label, so India alone looked provisional. The flag reads only
as a banner on the New Zealand firm: consent simulation and SIPs are India's, and a firm with no
`settings["personas"]` publishes no people on the login page.

    python -m seed.nz_demo_label_backfill

Idempotent: it sets one key where it is missing, on the firm with slug "demo".
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import SessionLocal
from app.models.tenant import Firm


async def run() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if firm is None:
            print("No demo firm here. Nothing to do.")
            return
        if (firm.settings or {}).get("demonstration"):
            print("The demo firm is already marked a demonstration.")
            return
        firm.settings = {**(firm.settings or {}), "demonstration": True}
        flag_modified(firm, "settings")
        await s.commit()
        print("Marked the demo firm as a demonstration.")


if __name__ == "__main__":
    asyncio.run(run())
