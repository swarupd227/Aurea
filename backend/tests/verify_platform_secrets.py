"""Credentials stay out of the code and the logs (B-144, B-154).

    python -m tests.verify_platform_secrets

Checks that:

  - the platform owner's password is not in the code: the bootstrap creates the account only from
    AUREA_SUPERADMIN_PASSWORD, of at least twelve characters, and says so when it is not set;
  - `seed.rotate_superadmin` changes an existing owner's password, refuses a short or mistyped one, writes the change
    to the audit log without the password, and says when there is no owner to change;
  - an invite link reaches the admin who asked, in the reply, and never the log, for a firm's admin and for the
    platform owner provisioning a firm.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

import app.models  # noqa: F401  - registers every table on Base.metadata
from app import db_bootstrap
from app.api import superadmin
from app.aurea_core import platform_admin
from app.core import config
from app.core.db import Base
from app.core.security import hash_password, verify_password
from app.models.enums import UserRole
from app.models.identity import AuditEvent, User
from app.models.tenant import Firm
from seed import rotate_superadmin

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def in_the_code() -> None:
    print("\n=== B-144: no password in the code ===")
    src = inspect.getsource(db_bootstrap)
    check("the old password is gone from the bootstrap", "aurea-super" in src, False)
    check("it creates the owner only from the setting", ("_settings.superadmin_password" in src, "superadmin_not_created" in src), (True, True))
    check("the setting is read from AUREA_SUPERADMIN_PASSWORD", config._ENV_ALIASES.get("superadmin_password"), "AUREA_SUPERADMIN_PASSWORD")
    check("and is empty unless someone sets it", config.Settings().superadmin_password, "")
    check("twelve characters at least", db_bootstrap.SUPERADMIN_MIN_LENGTH, 12)

    print("\n=== B-154: no invite link in a log ===")
    src = inspect.getsource(platform_admin.invite_user)
    check("a firm's invite logs who and when it lapses, not the link", ("accept-invite?token" in src, "expires_days" in src), (False, True))
    src = inspect.getsource(superadmin)
    log_lines = [ln for ln in src.splitlines() if "get_logger().info" in ln]
    check("provisioning a firm logs no invite link", any("invite_url" in ln or "token" in ln for ln in log_lines), False)
    check("  and still hands the link to the platform owner in its reply", '"invite_url": invite_url' in src, True)


async def behaviour() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    rotate_superadmin.SessionLocal = Session
    try:
        print("\n=== rotating the platform owner's password ===")
        check("with no owner on file it says so and changes nothing",
              "No superadmin@aurea.platform" in await rotate_superadmin.rotate("a-long-new-password"), True)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Mine", slug="mine", base_currency="NZD")
            s.add(firm)
            await s.flush()
            owner = User(id=uuid.uuid4(), firm_id=firm.id, email=rotate_superadmin.EMAIL, full_name="Platform Superadmin",
                         hashed_password=hash_password("the-old-one-in-git"), role=UserRole.SUPERADMIN, is_active=True)
            admin = User(id=uuid.uuid4(), firm_id=firm.id, email="ada@t.test", full_name="Ada", hashed_password=hash_password("pw"),
                         role=UserRole.ADMIN, is_active=True)
            s.add_all([owner, admin])
            await s.commit()

        said = await rotate_superadmin.rotate("a-long-new-password")
        async with Session() as s:
            now = (await s.execute(select(User).where(User.email == rotate_superadmin.EMAIL))).scalar_one()
            check("the new password signs in and the old one does not",
                  (verify_password("a-long-new-password", now.hashed_password), verify_password("the-old-one-in-git", now.hashed_password)),
                  (True, False))
            check("it says so, and to sign out elsewhere", ("Changed the password" in said, "Sign out" in said), (True, True))
            event = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "platform.superadmin_password_rotated"))).scalar_one()
            check("the change is on the audit log, naming the account", event.subject, rotate_superadmin.EMAIL)
            check("  and not the password", "a-long-new-password" in str(event.detail), False)

        typed = iter([])
        rotate_superadmin.getpass.getpass = lambda prompt="": next(typed)
        for label, answers in (("a short password is refused", ["short"]), ("two that differ are refused", ["a-long-new-password", "a-long-other-one"])):
            typed = iter(answers)
            try:
                rotate_superadmin.ask()
                check(label, "accepted", "refused")
            except SystemExit as exc:
                check(label, ("refused", "Nothing was changed" in str(exc)), ("refused", True))
        typed = iter(["a-long-new-password", "a-long-new-password"])
        check("the same twice, long enough, is taken", rotate_superadmin.ask(), "a-long-new-password")

        print("\n=== an invite in the log ===")
        async with Session() as s:
            firm = (await s.execute(select(Firm))).scalars().first()
            ada = (await s.execute(select(User).where(User.email == "ada@t.test"))).scalar_one()
            with capture_logs() as logs:
                _done, tok = await platform_admin.invite_user(s, firm, ada, email="new.person@t.test", full_name="New Person")
            check("the invite is logged", [e.get("event") for e in logs].count("mock_email_invite"), 1)
            check("  with no link or token in it", any(tok.token in str(e) or "accept-invite" in str(e) for e in logs), False)
    finally:
        await engine.dispose()


def main() -> int:
    in_the_code()
    asyncio.run(behaviour())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
