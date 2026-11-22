"""A filed US return on file, recorded from the document, and read by the US modules with its year (B-183).

    python -m tests.verify_tax_returns

The demo firm from seed/run.py in a throwaway SQLite file; the Morrisons are its US household:
  - `app.models` registers the table, so a deploy creates it;
  - a return is a US household's, names its document, and is checked: the year, the filing status, both spouses on a
    joint return and one person otherwise, each from the household, basis only for someone on it, a state's code, rates
    and carryforwards in bounds; a preview writes nothing;
  - with no return, the Roth room reads the profiles and says it is assumed;
  - recorded, the Roth room reads the return's filing status and taxable income, names its year and document, and
    works the taxable share of a conversion from Form 8606 basis; the capital loss carried forward and the marginal rate
    read off the return reach the US report;
  - a return is added and never edited: an amended one stands over the first for the same year, and an older year
    never stands over a newer one; it is on the ledger;
  - the conversation's Confirm card lays out what was read, and Astra reads the returns.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="aurea_returns_"), "returns.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")

import sqlalchemy.ext.asyncio as sa_async  # noqa: E402

_real_engine = sa_async.create_async_engine


def _engine(url, **kw):
    if str(url).startswith("sqlite"):
        for k in ("pool_size", "max_overflow", "pool_pre_ping"):
            kw.pop(k, None)
    return _real_engine(url, **kw)


sa_async.create_async_engine = _engine

from sqlalchemy import func, select  # noqa: E402

import app.models  # noqa: F401,E402
from app.aurea_core import firm_time, tax_intelligence, tax_returns  # noqa: E402
from app.aurea_core.graph import household_brain  # noqa: E402
from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.tools import TOOLS, ToolChangeState  # noqa: E402
from app.llm.executors import ExecutorError, ToolExecutors  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.graph import Household, Person  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.tax_return import TaxReturn  # noqa: E402
from app.models.tenant import Firm  # noqa: E402

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro) -> None:
    try:
        await coro
        check(label, "allowed", "refused")
    except tax_returns.TaxReturnError as e:
        check(label, "refused", "refused")
        print(f"         ({e})")


def registered() -> None:
    code = "import app.models; from app.core.db import Base; print('tax_return' in Base.metadata.tables)"
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://check:check@127.0.0.1:1/check"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).stdout.strip().splitlines()
    print("\n=== The table a deploy creates ===")
    check("app.models registers tax_return", out[-1] if out else None, "True")


async def roth(s, firm, hh) -> dict:
    brain = await household_brain(s, hh.id, firm_id=firm.id)
    return tax_intelligence._us_roth_conversion(brain, today=firm_time.today(firm)), brain


async def main() -> int:
    registered()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from seed import run as seed_run
    await seed_run.seed()

    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).order_by(Firm.created_at))).scalars().first()
        adviser = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADVISER))).scalars().first()
        us = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Morrison%")))).scalars().first()
        nz = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name.like("%Chen%")))).scalars().first()
        robert = (await s.execute(select(Person).where(Person.household_id == us.id, Person.full_name.like("Robert%")))).scalars().first()
        patricia = (await s.execute(select(Person).where(Person.household_id == us.id, Person.full_name.like("Patricia%")))).scalars().first()
        other = (await s.execute(select(Person).where(Person.household_id == nz.id))).scalars().first()
        year = firm_time.today(firm).year - 1
        both = [str(robert.id), str(patricia.id)]
        joint = dict(person_ids=both, tax_year=year, filing_status="married_filing_jointly", agi=1_020_000,
                     taxable_income=900_000, marginal_rate=0.35, effective_rate=0.27, qbi_deduction=12_000,
                     carryforwards=[{"kind": "capital_loss", "amount": 25_000}],
                     basis_8606=[{"person_id": str(robert.id), "amount": 50_000}], state="CA",
                     source_document=f"Form 1040 for {year}, Robert and Patricia Morrison, as filed")

        print("\n=== Before any return ===")
        r0, _ = await roth(s, firm, us)
        # The seeded profile says "married_joint", which is no filing status the brackets know: not assessed, and said.
        check("a profile's unknown filing status is not assessed", (r0["status"], r0["filing_status"]), ("not_assessed", "married_joint"))
        check("and says the income would be the profiles'", "taxable_income" in r0["assumed"], True)

        print("\n=== What a return needs ===")
        await refused("a US household", tax_returns.prepare(s, firm, nz.id, **{**joint, "person_ids": [str(other.id)], "filing_status": "single"}))
        await refused("both spouses on a joint return", tax_returns.prepare(s, firm, us.id, **{**joint, "person_ids": [str(robert.id)]}))
        await refused("one person on a return that is not joint", tax_returns.prepare(s, firm, us.id, **{**joint, "filing_status": "single"}))
        await refused("people of this household", tax_returns.prepare(s, firm, us.id, **{**joint, "person_ids": [str(robert.id), str(other.id)]}))
        await refused("a year that has happened", tax_returns.prepare(s, firm, us.id, **{**joint, "tax_year": year + 5}))
        await refused("a filing status the law has", tax_returns.prepare(s, firm, us.id, **{**joint, "filing_status": "joint"}))
        await refused("the document it is read from", tax_returns.prepare(s, firm, us.id, **{**joint, "source_document": ""}))
        await refused("basis for someone on the return", tax_returns.prepare(s, firm, us.id, **{**joint, "basis_8606": [{"person_id": str(other.id), "amount": 1}]}))
        await refused("a state's code", tax_returns.prepare(s, firm, us.id, **{**joint, "state": "Cali"}))
        await refused("a rate in bounds", tax_returns.prepare(s, firm, us.id, **{**joint, "marginal_rate": 0.9}))
        await refused("a carryforward the return has", tax_returns.prepare(s, firm, us.id, **{**joint, "carryforwards": [{"kind": "magic", "amount": 1}]}))
        await refused("taxable income", tax_returns.prepare(s, firm, us.id, **{**joint, "taxable_income": None}))
        ready = await tax_returns.prepare(s, firm, us.id, **{**joint, "marginal_rate": 35})
        check("a rate given as 35 reads as 35%", ready["marginal_rate"], 0.35)
        check("a preview writes nothing", (await s.execute(select(func.count(TaxReturn.id)))).scalar(), 0)

        print("\n=== The Confirm card ===")
        ex = ToolExecutors(s, adviser.id, adviser.role, firm.id)
        card = await ex.preview_change("record_tax_return", {"household_id": str(us.id), **joint})
        rows = {r["label"]: r["value"] for r in (card or {}).get("rows", [])}
        check("it names the return", rows.get("Return"), f"{year} · married filing jointly")
        check("and the taxable income", rows.get("Taxable income"), "$900,000")
        check("and the 8606 basis by person", rows.get(f"Form 8606 basis, {robert.full_name}"), "$50,000")
        check("and the document", rows.get("Document"), joint["source_document"])
        try:
            await ex.preview_change("record_tax_return", {"household_id": str(us.id), **joint, "source_document": ""})
            check("a return with no document is no card", "card", "refused")
        except ExecutorError:
            check("a return with no document is no card", "refused", "refused")
        check("an adviser or paraplanner records one", TOOLS["record_tax_return"].roles_required, {UserRole.ADVISER, UserRole.PARAPLANNER})
        check("recording waits on a Confirm card", TOOLS["record_tax_return"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
        check("no client holds either", any(UserRole.CLIENT in TOOLS[k].roles_required for k in ("record_tax_return", "read_tax_returns")), False)

        print("\n=== Recorded, the US modules read it ===")
        await ex.record_tax_return({"household_id": str(us.id), **joint})
        await s.commit()
        r1, brain = await roth(s, firm, us)
        c1 = r1["candidates"][0]
        check("one return stands", len(brain["tax_returns"]), 1)
        check("the Roth room reads the return", (c1["income_source"], c1["return_year"], c1["taxable_income"]), ("return", year, 900_000.0))
        check("and names its document", c1["return_document"], joint["source_document"])
        check("nothing about it is assumed", r1["assumed"], {})
        check("the filing status is the return's", (r1["filing_status"], r1["filing_status_recorded"]), ("married_filing_jointly", True))
        check("8606 basis gives the taxable share", c1["taxable_share"], round(1 - 50_000 / c1["ira_balance"], 4))
        check("and the words say it", "Form 8606 basis" in c1["action"] and f"The {year} return's income" in c1["action"], True)
        report = tax_intelligence._us_tax_report(brain, [], today=firm_time.today(firm))
        check("the capital loss carried forward reaches the report", report["carryforwards"]["capital_loss"], 25_000.0)
        check("with the return it is on", report["returns_on_file"][0]["tax_year"], year)
        check("the marginal rate is the return's", tax_intelligence._us_marginal(brain, {"id": str(robert.id), "profile": {}}), 0.35)

        print("\n=== Added, never edited ===")
        await tax_returns.record(s, firm, us.id, by="Test", **{**joint, "taxable_income": 880_000,
                                                               "source_document": f"Form 1040-X for {year}, as amended"})
        await tax_returns.record(s, firm, us.id, by="Test", **{**joint, "tax_year": year - 1, "taxable_income": 700_000,
                                                               "source_document": f"Form 1040 for {year - 1}, as filed"})
        await s.commit()
        listed = await tax_returns.for_household(s, us.id, firm_id=firm.id)
        check("three on file", len(listed), 3)
        check("the first is kept as it was", sorted(r["taxable_income"] for r in listed), [700_000.0, 880_000.0, 900_000.0])
        r2, brain = await roth(s, firm, us)
        check("the amended return stands for its year", r2["candidates"][0]["taxable_income"], 880_000.0)
        check("an older year never stands over a newer", r2["candidates"][0]["return_year"], year)
        from app.models.governance import LedgerEntry
        check("each is on the ledger", (await s.execute(select(func.count(LedgerEntry.id)).where(LedgerEntry.event_type == "tax_return_recorded"))).scalar(), 3)

        print("\n=== The conversation reads them ===")
        read = await ToolExecutors(s, adviser.id, adviser.role, firm.id).read_tax_returns({"household_id": str(us.id)})
        check("Astra reads every return, newest first", [r["tax_year"] for r in read["returns"]], [year, year, year - 1])
        check("and marks the one that stands", [r["standing"] for r in read["returns"]], [True, False, False])
        check("and says none on file is not none filed", "does not mean none was filed" in read["message"], True)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
