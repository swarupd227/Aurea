"""A Confirm card past its time reads Expired when it is shown, not when it is next clicked (B-51).

    python -m tests.verify_lapsed_confirmations

Against a throwaway SQLite database, through the real routes and gateway. Checks that:

  - a confirmation within its 24 hours stays waiting, with when it lapses on the card;
  - one past its time reads Expired as soon as its thread is read, with no buttons to offer;
  - the conversation list and Needs you no longer count it, and the briefing's count drops with it;
  - a read only touches the reader's own cards: another person's lapsed card waits until theirs is read;
  - a click on a lapsed card is refused and runs nothing;
  - the next turn tells the model it was not done because its confirmation expired.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import threads as T
from app.core.db import Base
from app.llm import pending
from app.llm.gateway import Gateway, GatewayError
from app.models.enums import UserRole
from app.models.identity import User
from app.models.tenant import Firm
from app.models.thread import (MessageRole, PendingAction, PendingActionStatus, Thread, ThreadKind, ThreadMessage, ToolCall,
                               ToolCallStatus)

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def main() -> int:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Mine", slug="mine", base_currency="NZD")
            s.add(firm)
            await s.flush()

            def user(name):
                return User(id=uuid.uuid4(), firm_id=firm.id, email=f"{uuid.uuid4().hex}@x.test", full_name=name,
                            hashed_password="x", role=UserRole.ADVISER, is_active=True)
            sophie, ben = user("Sophie Tran"), user("Ben Ade")
            s.add_all([sophie, ben])
            await s.flush()
            now = datetime.utcnow()

            async def paused(who, *, age_hours: float, tool="create_task"):
                t = Thread(firm_id=firm.id, kind=ThreadKind.ASK_ASTRA, created_by_user_id=who.id, title=f"{age_hours}h")
                s.add(t)
                await s.flush()
                m = ThreadMessage(thread_id=t.id, role=MessageRole.ASTRA, text="Adding it.", suggestions=[])
                s.add(m)
                await s.flush()
                tc = ToolCall(message_id=m.id, tool_key=tool, inputs={"title": "Call the Chens"})
                s.add(tc)
                await s.flush()
                made = now - timedelta(hours=age_hours)
                p = PendingAction(thread_id=t.id, tool_call_id=tc.id, confirmation_text="Add a task?", requires_user_id=who.id,
                                  created_at=made, expires_at=made + timedelta(hours=24))
                s.add(p)
                await s.commit()
                return t, p

            fresh_t, fresh = await paused(sophie, age_hours=2)
            old_t, old = await paused(sophie, age_hours=30)
            bens_t, bens = await paused(ben, age_hours=30)

            print("\n=== within its time ===")
            card = (await T.get_thread(fresh_t.id, user=sophie, session=s))["messages"][0]["pending_action"]
            check("it is still waiting", card["decision"], None)
            check("and the card says when it lapses, as UTC", (card["expires_at"].endswith("Z"),
                  abs(datetime.fromisoformat(card["expires_at"][:-1]) - (now + timedelta(hours=22))) < timedelta(seconds=5)), (True, True))

            print("\n=== past it, before anyone clicks ===")
            b = await T.briefing(user=sophie, session=s)
            check("the briefing counts the one still waiting, not the lapsed one", b["rows"][0]["count"], 1)
            n = await T.needs_you(user=sophie, session=s)
            check("Needs you lists only the fresh one", (n["needs_decision_count"], [i["id"] for i in n["needs_decision"]]), (1, [str(fresh.id)]))
            listed = {t["id"]: t["status"] for t in (await T.list_threads(user=sophie, session=s))["threads"]}
            check("the conversation list shows the lapsed thread as idle", (listed[str(old_t.id)], listed[str(fresh_t.id)]), ("idle", "awaiting_confirmation"))
            card = (await T.get_thread(old_t.id, user=sophie, session=s))["messages"][0]["pending_action"]
            check("its card reads Expired", card["decision"], "expired")
            await s.commit()
            await s.refresh(old)
            check("and it is expired on record", old.status, PendingActionStatus.EXPIRED)

            print("\n=== only the reader's own ===")
            await s.refresh(bens)
            check("Ben's lapsed card waits until his is read", bens.status, PendingActionStatus.AWAITING_CONFIRMATION)
            await T.list_threads(user=ben, session=s)
            await s.commit()
            await s.refresh(bens)
            check("  then it lapses", bens.status, PendingActionStatus.EXPIRED)
            try:
                await pending.lapse(s)
                check("a lapse with no scope is refused", "allowed", "refused")
            except ValueError:
                check("a lapse with no scope is refused", "refused", "refused")

            print("\n=== a click on a lapsed card ===")
            _, late = await paused(sophie, age_hours=25)
            try:
                await Gateway(s, sophie.id, sophie.role, firm.id).confirm_pending_action(late.id, True)
                check("Confirm is refused", "allowed", "refused")
            except GatewayError as exc:
                check("Confirm is refused, saying it expired", "expired" in str(exc), True)
            tc = (await s.execute(select(ToolCall).where(ToolCall.id == late.tool_call_id))).scalar_one()
            check("  and nothing ran", (tc.status, tc.result), (ToolCallStatus.PENDING, None))

            print("\n=== the model is told ===")
            g = Gateway(s, sophie.id, sophie.role, firm.id)
            msgs = (await s.execute(select(ThreadMessage).where(ThreadMessage.thread_id == old_t.id))).scalars().all()
            said = await g._not_done(list(msgs))
            check("the next turn reads it as not done because its confirmation expired",
                  any("its confirmation expired" in x for x in sum(said.values(), [])), True)
    finally:
        await engine.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
