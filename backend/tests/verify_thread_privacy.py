"""A conversation belongs to the person who started it.

    python -m tests.verify_thread_privacy

Threads were scoped only by firm, so every staff member's rail listed every colleague's
conversations, and opening one showed tool results gated to its author's role (an adviser could
read an administrator's or compliance's). Another person's thread now reads as not found - in
the list, the briefing count, a direct read, a send through the route and through the gateway.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.core.db import Base
from app.llm.gateway import Gateway, GatewayError
from app.models.enums import UserRole
from app.models.identity import User
from app.models.tenant import Firm
from app.models.thread import MessageRole, Thread, ThreadKind, ThreadMessage

PASS, FAIL = [], []
R = UserRole


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def status_of(coro) -> int:
    try:
        await coro
        return 200
    except HTTPException as exc:
        return exc.status_code


async def behaviour(Session) -> None:
    from app.api import threads as T

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Mine", slug="mine")
        other = Firm(id=uuid.uuid4(), name="Theirs", slug="theirs")
        s.add_all([firm, other])
        await s.flush()

        def user(role, f=firm):
            u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{role.value}-{uuid.uuid4().hex[:6]}@x",
                     full_name=role.value, hashed_password="x", role=role)
            s.add(u)
            return u
        adviser, adviser2, admin = user(R.ADVISER), user(R.ADVISER), user(R.ADMIN)
        outsider = user(R.ADVISER, other)
        await s.flush()

        def thread(owner, title):
            t = Thread(firm_id=owner.firm_id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=owner.id, title=title)
            s.add(t)
            return t
        mine = thread(adviser, "Chen performance")
        admins = thread(admin, "Fetch prices now.")
        colleague = thread(adviser2, "Wu billing")
        await s.flush()
        s.add(ThreadMessage(thread_id=admins.id, role=MessageRole.ASTRA, text="Refreshed 928 prices.", suggestions=[]))
        await s.commit()

        print("\n=== the rail lists only your own conversations ===")
        listed = [t["title"] for t in (await T.list_threads(user=adviser, session=s))["threads"]]
        check("the adviser sees their own", listed, ["Chen performance"])
        check("the administrator sees only theirs",
              [t["title"] for t in (await T.list_threads(user=admin, session=s))["threads"]], ["Fetch prices now."])
        check("someone with none sees none", (await T.list_threads(user=outsider, session=s))["threads"], [])

        print("\n=== the briefing counts only your own ===")
        row = next(r for r in (await T.briefing(user=adviser, session=s))["rows"] if r["id"] == "conversations")
        check("conversations on the adviser's home", row["count"], 1)

        print("\n=== another person's conversation reads as not found ===")
        check("the author can open it", await status_of(T.get_thread(mine.id, user=adviser, session=s)), 200)
        check("a colleague in another role cannot", await status_of(T.get_thread(admins.id, user=adviser, session=s)), 404)
        check("a colleague in the same role cannot", await status_of(T.get_thread(colleague.id, user=adviser, session=s)), 404)
        check("another firm cannot", await status_of(T.get_thread(mine.id, user=outsider, session=s)), 404)
        check("an administrator cannot read an adviser's", await status_of(T.get_thread(mine.id, user=admin, session=s)), 404)

        print("\n=== and cannot be written to ===")
        check("sending into a colleague's thread is refused",
              await status_of(T.send_message(admins.id, {"text": "what did you find?"}, user=adviser, session=s)), 404)
        try:
            evs = [e async for e in Gateway(s, adviser.id, adviser.role, firm.id).send_message(admins.id, "hi")]
            refused = any(e.get("type") == "error" for e in evs)
        except GatewayError:
            refused = True
        check("the gateway refuses it too", refused, True)
        check("and nothing was added to it",
              [m["text"] for m in (await T.get_thread(admins.id, user=admin, session=s))["messages"]],
              ["Refreshed 928 prices."])


async def main_async() -> None:
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "privacy.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c))
    try:
        await behaviour(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
        try:
            os.remove(path)
            os.rmdir(tmp)
        except OSError:
            pass


def main() -> int:
    asyncio.run(main_async())
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
