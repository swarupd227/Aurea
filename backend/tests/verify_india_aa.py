"""Prove India row 9: the Account Aggregator's consents, the gate a feed reads, and a consolidated statement a person supplies.

Covers a consent as a person recorded it (added once, ended once, never edited) with each refusal; its state from the firm's
day; the gate (`permitted`, `permits`) that keeps a pull inside the consent's kinds, person and dates; a pull that delivers
held-away lines through the feed registry, is safe to repeat, and ends what it delivered when the consent is withdrawn or has
lapsed; an e-CAS read from a document into lines, each checked, with refusals listed, a repeat refused, a newer statement
replacing the line it supersedes, a line the aggregator already delivers left to it, and the ISIN resolving only to the firm's
own instrument; that net worth counts a line once; the tools, their roles and Confirm cards; and that nothing in the core
imports a simulation.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_aa
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import account_aggregator as aa
from app.aurea_core import balance_sheet, firm_time, held_away, held_away_feeds
from app.aurea_core.held_away_feeds import FeedLine
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.aggregator import AaConsent, EcasStatement
from app.models.enums import AssetClass, MarketType, UserRole
from app.models.graph import Household, Person
from app.models.governance import LedgerEntry
from app.models.held_away import HeldAwayHolding
from app.models.identity import User
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.simulations import tag

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None, exc=(aa.AggregatorError,)) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:100]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


class TestFeed:
    """A financial information user's client, in small: it reads the gate and delivers only what a consent allows."""
    key = "test:aggregator"
    name = "Test aggregator"
    simulated = False
    account_aggregator = True

    def __init__(self, household_id, person_id):
        self.household_id, self.person_id = household_id, person_id

    def enabled_for(self, firm) -> bool:
        return True

    async def lines(self, session, firm):
        today = firm_time.today(firm)
        active = await aa.permitted(session, firm, today)
        out = []
        for c in active:
            for fi, isin, name, value in (("mutual_funds", "INF879O01027", "Flexi Cap Fund - Direct", 100000), ("equities", "INE002A01018", "A Listed Company", 50000)):
                on = min(today - timedelta(days=5), c.data_to)
                if aa.permits(c, fi_type=fi, value_on=on, person_id=self.person_id, household_id=self.household_id):
                    out.append(FeedLine(external_id=f"{aa.external_prefix(c)}{isin}", household_id=self.household_id, person_id=self.person_id,
                                        institution="Test AMC", instrument_name=name, value=value, currency="INR", value_on=on,
                                        document=f"Test feed under {c.artefact_id}", isin=isin, account_label="Folio 1"))
        await aa.mark_pulled(session, active, today)
        return out


def terms(hh, **over):
    t = dict(household_id=hh.id, artefact_id="AA-TEST-0001", aggregator="Test Aggregator", purpose_code="101", fi_types=["mutual_funds"],
             data_from="2025-11-01", data_to="2026-10-31", expires_on="2027-06-30", document="Consent notice, filed in the client folder")
    t.update(over)
    return t


CAS = """account,isin,name,units,value,institution,holder
Folio 111/22,INF179K01DC2,HDFC Corporate Bond Fund - Regular Plan - Growth,1000,34140,HDFC Mutual Fund,Rajesh Sharma
Folio 333/44,INF789F01JN2,UTI Nifty 50 Index Fund - Regular Plan - Growth,500,81285,UTI Mutual Fund,Meera Sharma
"""


async def main() -> int:
    print("\n=== The consent's states, from the firm's day ===")
    c = AaConsent(consent_start=date(2026, 6, 1), expires_on=date(2027, 6, 1), revoked_on=None)
    check("before it starts", aa.state_of(c, date(2026, 5, 31)), "not_started")
    check("on its day and until it lapses", (aa.state_of(c, date(2026, 6, 1)), aa.state_of(c, date(2027, 6, 1))), ("active", "active"))
    check("the day after it lapses", aa.state_of(c, date(2027, 6, 2)), "expired")
    c.revoked_on = date(2026, 7, 1)
    check("withdrawn beats the dates", aa.state_of(c, date(2026, 8, 1)), "revoked")
    check("the ISIN pattern takes a fund and a share and not a typo",
          [bool(aa.ISIN.match(x)) for x in ("INF179K01DC2", "INE002A01018", "INF179K01DC", "US0378331005")], [True, True, False, False])
    check("nothing in the core imports a simulation",
          [p.name for p in (pathlib.Path(__file__).resolve().parents[1] / "app" / "aurea_core").glob("*.py")
           if re.search(r"^\s*(from|import)\s+app\.simulations", p.read_text(encoding="utf-8"), re.M)], [])

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="IN", slug="in", base_currency="INR", jurisdiction="IN", settings={"timezone": "Asia/Kolkata"})
            nz = Firm(id=uuid.uuid4(), name="NZ", slug="nz", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, nz])
            await s.flush()
            today = firm_time.today(firm)
            users = {r: User(id=uuid.uuid4(), firm_id=firm.id, email=f"{r.value}@in", full_name=r.value, role=r, hashed_password="x")
                     for r in (UserRole.ADVISER, UserRole.COMPLIANCE, UserRole.OPERATIONS)}
            s.add_all(users.values())
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Sharmas")
            other_hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Iyers")
            nz_hh = Household(id=uuid.uuid4(), firm_id=nz.id, name="The Chens")
            s.add_all([hh, other_hh, nz_hh])
            await s.flush()
            rajesh = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Rajesh Sharma")
            meera = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Meera Sharma")
            stranger = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=other_hh.id, full_name="Karthik Iyer")
            s.add_all([rajesh, meera, stranger])
            fund = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="UTI-NIFTY50-REGULAR", name="UTI Nifty 50 Index Fund - Regular", asset_class=AssetClass.EQUITY,
                              market_type=MarketType.PUBLIC, currency="INR", values_tags={"isin": "INF789F01JN2"})
            s.add(fund)
            await s.flush()
            d = lambda n: (today + timedelta(days=n)).isoformat()    # noqa: E731

            print("\n=== Recording a consent, and what is refused ===")
            good = terms(hh, data_from=d(-300), data_to=d(-10), expires_on=d(200))
            await refused("a firm outside India has no aggregator", aa.record_consent(s, nz, by="x", **{**good, "household_id": nz_hh.id}), "indian firm")
            await refused("an artefact id is given", aa.record_consent(s, firm, by="x", **{**good, "artefact_id": "ab"}), "artefact")
            await refused("a purpose is one of the list", aa.record_consent(s, firm, by="x", **{**good, "purpose_code": "999"}), "purpose")
            await refused("what it covers is named, from the list", aa.record_consent(s, firm, by="x", **{**good, "fi_types": ["bonds"]}), "covers")
            await refused("a data range cannot run backwards", aa.record_consent(s, firm, by="x", **{**good, "data_from": d(-5), "data_to": d(-50)}), "starts after")
            await refused("a data range cannot end in the future", aa.record_consent(s, firm, by="x", **{**good, "data_to": d(3)}), "future")
            await refused("a consent already lapsed is not recorded as one", aa.record_consent(s, firm, by="x", **{**good, "expires_on": d(-1)}), "lapsed")
            await refused("it needs its evidence", aa.record_consent(s, firm, by="x", **{**good, "document": "x"}), "filed")
            await refused("a person must be in the household", aa.record_consent(s, firm, by="x", **{**good, "person_id": stranger.id}), "not in the household")
            await refused("another firm's household is refused", aa.record_consent(s, firm, by="x", **{**good, "household_id": nz_hh.id}), "not this firm")
            out = await aa.record_consent(s, firm, by="adviser@in", **good)
            check("it is recorded, active, with its kinds and purpose", (out["counts"]["active"], out["consents"][0]["fi_labels"], out["consents"][0]["purpose"]),
                  (1, ["Mutual funds"], "Wealth management service"))
            await refused("the same artefact twice", aa.record_consent(s, firm, by="x", **good), "already recorded")
            check("it wrote a ledger entry", (await s.execute(select(func.count()).select_from(LedgerEntry))).scalar_one() >= 1, True)

            print("\n=== The gate: kinds, person and dates ===")
            row = (await s.execute(select(AaConsent))).scalars().one()
            ok = lambda **k: aa.permits(row, **{"fi_type": "mutual_funds", "value_on": today - timedelta(days=20), "person_id": rajesh.id, "household_id": hh.id, **k})  # noqa: E731
            check("a kind it covers, in its range, for the household", ok(), True)
            check("a kind it does not cover", ok(fi_type="equities"), False)
            check("a date before its range", ok(value_on=today - timedelta(days=400)), False)
            check("a date after its range", ok(value_on=today - timedelta(days=2)), False)
            check("another household", ok(household_id=other_hh.id), False)
            row.person_id = meera.id
            check("a consent for one person delivers only theirs", (ok(person_id=meera.id), ok(person_id=rajesh.id)), (True, False))
            row.person_id = None

            print("\n=== A pull stays inside the consent and is safe to repeat ===")
            feed = TestFeed(hh.id, rajesh.id)
            held_away_feeds.register(feed)
            try:
                check("with a source connected, a pull is possible", [f.key for f in held_away_feeds.for_firm(firm) if getattr(f, "account_aggregator", False)], [feed.key])
                r = await aa.pull(s, firm, by="adviser@in")
                check("only the mutual fund, not the share the consent leaves out", (r["added"], r["consents_used"]), (1, 1))
                line = (await s.execute(select(HeldAwayHolding).where(HeldAwayHolding.firm_id == firm.id))).scalars().one()
                check("the line carries the feed, its id under the consent, and its statement date",
                      (line.source_system, line.external_id.startswith("aa:AA-TEST-0001:"), line.status), (feed.key, True, "current"))
                r2 = await aa.pull(s, firm, by="adviser@in")
                check("a second pull adds nothing", (r2["added"], r2["unchanged"]), (0, 1))
                check("the consent counts its pulls", (await aa.overview(s, firm))["consents"][0]["pulls"], 2)
                check("and its lines", (await aa.overview(s, firm))["consents"][0]["lines"], 1)

                print("\n=== A consolidated statement the aggregator already delivers is left to it ===")
                await refused("an e-CAS with no document is refused", aa.record_ecas(s, firm, by="x", household_id=hh.id, kind="nsdl", statement_date=d(-9),
                                                                                       document="", lines_text=CAS), "statement")
                dup = "account,isin,name,units,value,institution,holder\nFolio 1,INF879O01027,Flexi Cap Fund - Direct,10,100000,Test AMC,Rajesh Sharma\n"
                await refused("nothing left but what the aggregator holds", aa.record_ecas(
                    s, firm, by="x", household_id=hh.id, kind="cams_kfintech", statement_date=d(-9), document="CAMS CAS, filed", lines_text=dup),
                    "no line could be read")

                print("\n=== Withdrawn, a consent delivers nothing and what it delivered ends ===")
                await refused("a reason is needed to end it", aa.revoke_consent(s, firm, by="x", consent_id=row.id, reason=""), "why")
                out = await aa.revoke_consent(s, firm, by="adviser@in", consent_id=row.id, reason="The client withdrew it in the aggregator's app", document="Notice 2026-11-20")
                check("it reads withdrawn", (out["consents"][0]["state"], out["counts"]["revoked"]), ("revoked", 1))
                await refused("it cannot be withdrawn twice", aa.revoke_consent(s, firm, by="x", consent_id=row.id, reason="again"), "was withdrawn")
                check("the gate no longer lists it", await aa.permitted(s, firm), [])
                r3 = await aa.pull(s, firm, by="adviser@in")
                check("a pull delivers nothing and ends the line, kept", (r3["added"], r3["ended"], r3["consents_used"]), (0, 1, 0))
                ended = (await s.execute(select(HeldAwayHolding).where(HeldAwayHolding.firm_id == firm.id))).scalars().one()
                check("the line is on file, ended, with why", (ended.status, ended.ended_reason), ("ended", "No longer delivered by the feed"))
                await s.execute(HeldAwayHolding.__table__.delete())

                print("\n=== A lapsed consent delivers nothing either ===")
                await aa.record_consent(s, firm, by="adviser@in", **terms(hh, artefact_id="AA-TEST-0002", data_from=d(-300), data_to=d(-10), expires_on=d(5)))
                check("active while it stands", (await aa.pull(s, firm, by="x"))["added"], 1)
                lapsing = (await s.execute(select(AaConsent).where(AaConsent.artefact_id == "AA-TEST-0002"))).scalars().one()
                lapsing.expires_on = today - timedelta(days=1)
                await s.flush()
                r4 = await aa.pull(s, firm, by="x")
                check("once it has lapsed the line ends and none is added", (r4["added"], r4["ended"], r4["consents_used"]), (0, 1, 0))
                check("the overview reads it lapsed", [c["state"] for c in (await aa.overview(s, firm))["consents"] if c["artefact_id"] == "AA-TEST-0002"], ["expired"])
            finally:
                held_away_feeds.unregister(feed.key)
            await refused("with no source connected a pull says so", aa.pull(s, firm, by="x"), "no account aggregator source")

            print("\n=== A consolidated statement a person supplies ===")
            lines, bad = aa.parse_cas(CAS + "Folio 9,INF000000XX,A Typo,10,1000,Some AMC,Rajesh Sharma\nFolio 8,INF879O01027,No Value,10,,Some AMC,Rajesh Sharma\n")
            check("two lines read, two refused with their rows", (len(lines), [b["row"] for b in bad]), (2, [4, 5]))
            check("a refusal says why", ("not an ISIN" in bad[0]["reason"], "value" in bad[1]["reason"]), (True, True))
            await refused("a header that lacks a column is refused", aa.record_ecas(s, firm, by="x", household_id=hh.id, kind="nsdl", statement_date=d(-9),
                                                                                      document="NSDL CAS, filed", lines_text="isin,name\nINF879O01027,X\n"), "missing")
            await refused("a statement date in the future", aa.record_ecas(s, firm, by="x", household_id=hh.id, kind="nsdl", statement_date=d(3),
                                                                            document="NSDL CAS, filed", lines_text=CAS), "future")
            await refused("a kind that is not a CAS", aa.record_ecas(s, firm, by="x", household_id=hh.id, kind="bank", statement_date=d(-9),
                                                                     document="NSDL CAS, filed", lines_text=CAS), "one of")
            await refused("a holder who is not in the household", aa.record_ecas(
                s, firm, by="x", household_id=hh.id, kind="nsdl", statement_date=d(-9), document="NSDL CAS, filed",
                lines_text=CAS.replace("Meera Sharma", "Karthik Iyer").replace("Rajesh Sharma", "Karthik Iyer")), "no line could be read")
            before = (await s.execute(select(func.count()).select_from(HeldAwayHolding))).scalar_one()
            out = await aa.record_ecas(s, firm, by="operations@in", household_id=hh.id, kind="cams_kfintech", statement_date=d(-9),
                                       document="CAMS CAS for the Sharmas, October, filed in the client folder", lines_text=CAS)
            check("two lines recorded, none replaced", (out["added"], out["replaced"], out["total"]), (2, 0, 115425.0))
            rows = (await s.execute(select(HeldAwayHolding).where(HeldAwayHolding.status == "current"))).scalars().all()
            check("each is the statement's figure, as a person recorded it (no feed), with its folio and ISIN",
                  sorted((r.account_label, r.isin, float(r.value), r.source_system) for r in rows),
                  [("Folio 111/22", "INF179K01DC2", 34140.0, None), ("Folio 333/44", "INF789F01JN2", 81285.0, None)])
            uti = next(r for r in rows if r.isin == "INF789F01JN2")
            check("an ISIN resolves to the firm's own instrument and to nothing else", (uti.symbol, next(r for r in rows if r.isin == "INF179K01DC2").symbol),
                  ("UTI-NIFTY50-REGULAR", None))
            check("an asset class is never guessed from an ISIN", next(r for r in rows if r.isin == "INF179K01DC2").asset_class, None)
            check("the statement is kept with what was read", len((await s.execute(select(EcasStatement))).scalars().one().lines), 2)
            await refused("the same statement twice", aa.record_ecas(s, firm, by="x", household_id=hh.id, kind="cams_kfintech", statement_date=d(-9),
                                                                      document="CAMS CAS, filed", lines_text=CAS), "already recorded")
            newer = CAS.replace("34140", "35000")
            out2 = await aa.record_ecas(s, firm, by="operations@in", household_id=hh.id, kind="cams_kfintech", statement_date=d(-2),
                                        document="CAMS CAS for the Sharmas, November, filed", lines_text=newer)
            check("a newer statement replaces the lines it supersedes, which stay on file", (out2["added"], out2["replaced"]), (0, 2))
            counts = {}
            for st in ("current", "ended"):
                counts[st] = (await s.execute(select(func.count()).select_from(HeldAwayHolding).where(HeldAwayHolding.status == st))).scalar_one()
            check("one current line each, and the two it replaced ended beside the aggregator's earlier one", (counts["current"], counts["ended"]), (2, 3))
            check("it carries no PAN", any("pan" in k.lower() for k in out2), False)
            check("the ledger names the statement", True, (await s.execute(select(func.count()).select_from(LedgerEntry).where(
                LedgerEntry.event_type == "ecas_recorded"))).scalar_one() == 2)

            print("\n=== Net worth counts a line once ===")
            sheet = await balance_sheet.for_household(s, hh.id, firm_id=firm.id)
            check("the lines are in net worth at what they state", (sheet["held_away_lines"]["count"], sheet["held_away_lines"]["value"]), (2, 116285.0))
            check("and held away is those lines alone", sheet["assets"]["held_away"], 116285.0)
            view = await held_away.for_household(s, hh.id, firm_id=firm.id)
            check("a line with no real price here has no performance", [h["performance"] for h in view["holdings"] if h["isin"] == "INF179K01DC2"], [None])

            print("\n=== The conversation: tools, roles and Confirm cards ===")
            for k in ("record_aggregator_consent", "revoke_aggregator_consent", "pull_aggregator", "record_ecas"):
                check(f"{k} stops for a Confirm", TOOLS[k].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("reading does not", TOOLS["read_aggregator"].change_state, ToolChangeState.NO)
            check("a client holds none of them", [k for k in ("read_aggregator", "record_aggregator_consent", "revoke_aggregator_consent", "pull_aggregator", "record_ecas")
                                                  if UserRole.CLIENT in TOOLS[k].roles_required], [])
            ex = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
            n = (await s.execute(select(func.count()).select_from(AaConsent))).scalar_one()
            card = await ex.preview_change("record_aggregator_consent", {**{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in terms(hh, artefact_id="AA-TEST-0003", data_from=d(-300), data_to=d(-10), expires_on=d(100), fi_types="mutual_funds, etfs").items()}})
            labels = [r["label"] for r in card["rows"]]
            check("the consent card names the aggregator, what it covers, the range and when it lapses",
                  all(x in labels for x in ("Aggregator", "Covers", "Data from", "Lapses", "Evidence")), True)
            check("showing it wrote nothing", (await s.execute(select(func.count()).select_from(AaConsent))).scalar_one(), n)
            await refused("compliance reads and does not record", ToolExecutors(s, users[UserRole.COMPLIANCE].id, UserRole.COMPLIANCE, firm.id).preview_change(
                "record_aggregator_consent", {**{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in terms(hh, artefact_id="AA-TEST-0004").items()}}), "adviser", (ExecutorError,))
            out = await ex.record_aggregator_consent({k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in terms(
                hh, artefact_id="AA-TEST-0003", data_from=d(-300), data_to=d(-10), expires_on=d(100), fi_types="mutual_funds, etfs").items()})
            check("confirmed, it is recorded once and reads active", ((await s.execute(select(func.count()).select_from(AaConsent))).scalar_one(), out["counts"]["active"]), (n + 1, 1))
            read = await ToolExecutors(s, users[UserRole.COMPLIANCE].id, UserRole.COMPLIANCE, firm.id).read_aggregator({})
            check("compliance reads the consents and the statements", (len(read["consents"]), len(read["statements"])), (3, 2))
            card = await ex.preview_change("record_ecas", {"household_id": str(hh.id), "kind": "nsdl", "statement_date": d(-3), "document": "NSDL CAS, October, filed",
                                                            "lines": "account,isin,name,units,value\nIN300001,INE002A01018,A Listed Company,10,2500\nbad,XX,Row,1,2\n"})
            check("the statement card names the lines read, the total and what was refused",
                  [r["label"] for r in card["rows"] if r["label"] in ("Lines read", "Total stated", "Refused")], ["Lines read", "Total stated", "Refused"])
            await refused("the card refuses what recording would", ex.preview_change("pull_aggregator", {}), "no account aggregator source", (ExecutorError,))
            nz_read = await ToolExecutors(s, uuid.uuid4(), UserRole.ADVISER, nz.id).read_aggregator({})
            check("a firm outside India has none", nz_read["applies"], False)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
