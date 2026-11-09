"""Change the platform owner's password, typed by the person running this and never stored in the code (B-144).

    python -m seed.rotate_superadmin

Run it inside the backend container (`az containerapp exec ... --command "/bin/bash"`). It asks for the new password
twice without echoing it, so it is not in the shell's history or the container's logs, and refuses one shorter than
twelve characters. Until it is run, the platform owner on a database seeded before 9 Nov 2026 still has the password
that used to be written in `app/db_bootstrap.py`, which is in the repository's history and so is not a secret.

The change goes on the audit log, naming the account and not the password. Tokens already issued stay valid until they
expire (`access_token_ttl_minutes`); sign out anywhere still signed in as the platform owner.
"""
from __future__ import annotations

import asyncio
import getpass
import sys

from sqlalchemy import select

import app.models  # noqa: F401
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.db_bootstrap import SUPERADMIN_MIN_LENGTH
from app.models.identity import AuditEvent, User

EMAIL = "superadmin@aurea.platform"


def ask() -> str:
    first = getpass.getpass("New platform owner password: ")
    if len(first) < SUPERADMIN_MIN_LENGTH:
        sys.exit(f"Refused: at least {SUPERADMIN_MIN_LENGTH} characters. Nothing was changed.")
    if getpass.getpass("Again: ") != first:
        sys.exit("Refused: the two did not match. Nothing was changed.")
    return first


async def rotate(password: str) -> str:
    async with SessionLocal() as s:
        owner = (await s.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
        if owner is None:
            return f"No {EMAIL} on this database; set AUREA_SUPERADMIN_PASSWORD and restart to create one."
        owner.hashed_password = hash_password(password)
        s.add(AuditEvent(firm_id=owner.firm_id, actor_id=None, actor_email="operator (seed.rotate_superadmin)",
                         event_type="platform.superadmin_password_rotated", subject=EMAIL,
                         detail={"note": "Password changed from the container; the password itself is not recorded."}))
        await s.commit()
        return f"Changed the password for {EMAIL}. Sign out anywhere still signed in as it."


def main() -> None:
    print(asyncio.run(rotate(ask())))


if __name__ == "__main__":
    main()
