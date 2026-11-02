"""Prove a campaign's consent comes from the change, that silence after the notice period is consent only where the
change allows it, that an objection or a bounced notice keeps a household out, that following an adviser always
needs a signature whatever the firm sets, and that answers are recorded from evidence and after the notice.

Runs against a throwaway SQLite database.

    python -m tests.verify_repapering
"""
from __future__ import annotations

import asyncio
import sys
import types
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import firm_time, repapering as rp
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, rp.RepaperingError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure() -> None:
    print("\n=== The consent table ===")
    firm = types.SimpleNamespace(settings={"repapering": {"adviser_recruitment": {"consent": "negative", "notice_days": 30},
                                                          "fee_increase": {"consent": "negative", "notice_days": 60}}})
    p = rp.policy(firm)
    check("a firm cannot make following an adviser silent", (p["adviser_recruitment"]["consent"], p["adviser_recruitment"]["notice_days"]), ("affirmative", None))
    check("it may set its own rule for a fee change", (p["fee_increase"]["consent"], p["fee_increase"]["notice_days"], p["fee_increase"]["set_by_firm"]), ("negative", 60, True))
    neg = types.SimpleNamespace(consent="negative", notice_days=30, sent_on=date(2026, 9, 1))
    check("silence inside the notice period is not yet consent", rp.standing(neg, [], date(2026, 9, 20))["state"], "in_notice_period")
    check("silence after it is", rp.standing(neg, [], date(2026, 10, 1)), {"state": "deemed_consent", "ready": True})
    check("an objection keeps them out", rp.standing(neg, [{"answer": "objected"}], date(2026, 10, 1)), {"state": "objected", "ready": False})
    check("a notice that came back is not consent", rp.standing(neg, [{"answer": "bounced"}], date(2026, 10, 1))["ready"], False)
    aff = types.SimpleNamespace(consent="affirmative", notice_days=None, sent_on=date(2026, 9, 1))
    check("for a signature, silence is no", rp.standing(aff, [], date(2027, 1, 1)), {"state": "awaiting_signature", "ready": False})


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US", settings={})
            s.add(firm)
            await s.flush()
            ops = User(id=uuid.uuid4(), firm_id=firm.id, email="ops@t", full_name="ops", role=UserRole.OPERATIONS, hashed_password="x")
            s.add(ops)
            hs = [Household(id=uuid.uuid4(), firm_id=firm.id, name=n) for n in ("Ames", "Brook", "Cole")]
            s.add_all(hs)
            await s.flush()
            ex = ToolExecutors(s, ops.id, ops.role, firm.id)
            today = firm_time.today(firm)

            print("\n=== A custodian conversion ===")
            ask = {"name": "Move to Pershing", "change_type": "custodian_conversion", "document": "Negative consent letter, Pershing conversion, v2"}
            await refused("the change is one of the table's", ex.preview_change("start_repapering", {**ask, "change_type": "rebrand"}), "one of")
            card = {r["label"]: r["value"] for r in (await ex.preview_change("start_repapering", ask))["rows"]}
            check("the card says silence for 30 days is consent, from the platform's table", (card["Consent"], card["Table"], card["Households"].split(":")[0]),
                  ("Negative: silence for 30 days after sending is consent", "The platform's", "3"))
            out = await ex.start_repapering(ask)
            cid = out["campaigns"][0]["id"]
            ids = {h.name: str(h.id) for h in hs}
            await refused("no answer before it is sent", ex.preview_change("record_repapering",
                          {"campaign_id": cid, "action": "objected", "household_ids": ids["Ames"], "evidence": "Letter from Ames"}), "sent before")
            sent = today - timedelta(days=35)
            await ex.record_repapering({"campaign_id": cid, "action": "sent", "on": sent.isoformat(), "evidence": "Mailing house manifest 0921"})
            await ex.record_repapering({"campaign_id": cid, "action": "objected", "on": (sent + timedelta(days=10)).isoformat(),
                                        "household_ids": ids["Ames"], "evidence": "Letter from Ames declining the move"})
            out = await ex.record_repapering({"campaign_id": cid, "action": "bounced", "on": (sent + timedelta(days=5)).isoformat(),
                                              "household_ids": ids["Brook"], "evidence": "Returned envelope, address unknown"})
            states = {h["household"]: h["state"] for h in out["campaigns"][0]["households"]}
            check("35 days on: Cole is deemed to consent, Ames objected, Brook's notice came back",
                  (states["Cole"], states["Ames"], states["Brook"], out["campaigns"][0]["ready"]), ("deemed_consent", "objected", "notice_bounced", 1))

            print("\n=== Following an adviser ===")
            out = await ex.start_repapering({"name": "Dana joins", "change_type": "adviser_recruitment", "document": "New advisory agreement and transfer form",
                                             "household_ids": f"{ids['Ames']},{ids['Cole']}"})
            cid2 = next(c["id"] for c in out["campaigns"] if c["name"] == "Dana joins")
            await ex.record_repapering({"campaign_id": cid2, "action": "sent", "on": (today - timedelta(days=60)).isoformat(), "evidence": "DocuSign batch 77"})
            out = await ex.record_repapering({"campaign_id": cid2, "action": "consented", "household_ids": ids["Cole"], "evidence": "Signed agreement, DocuSign 77-3"})
            camp = next(c for c in out["campaigns"] if c["id"] == cid2)
            states = {h["household"]: h["state"] for h in camp["households"]}
            check("sixty days of silence is still no; only Cole signed", (states["Ames"], states["Cole"], camp["ready"]), ("awaiting_signature", "consented", 1))
            await refused("a household outside the campaign is refused", ex.preview_change("record_repapering",
                          {"campaign_id": cid2, "action": "consented", "household_ids": ids["Brook"], "evidence": "Signed agreement"}), "not in this campaign")
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
