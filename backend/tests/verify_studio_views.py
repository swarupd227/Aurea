"""The Studio pages Astra could not reach (B-94): Tax, Estate, Family, Behavioural, Regulatory countdown,
Workforce, Skills and Book integration, as tools and as views beside the conversation.

    python -m tests.verify_studio_views

Against a throwaway SQLite database, this checks that:

  - each read changes nothing and is held by the roles its page's sidebar entry shows (and admin), parsed
    out of nav.ts; each write stops at a Confirm card that names the record, and a client holds none;
  - Family reads one firm only: another firm's household or person is not found, which the route did
    not check before;
  - Estate steps basis up only where the household's market says so, and never in a retirement account;
  - a skill stays at Tier 2, only its owner changes it, a copy is private, a disabled one does not run,
    and a run only proposes;
  - a new acquired book is marked as the sample it is, and only operations runs book integration on one
    batch, once;
  - the views are registered in the pane and open_view grounds a batch id.
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import book_batches, estate, family, firm_time
from app.core.db import Base
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState, tools_for_role, validate_tool_call
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.governance import Recommendation
from app.models.graph import Account, Household, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.skill import Skill
from app.models.tenant import Firm
from app.skills import library

PASS, FAIL = [], []
R = UserRole
ROOT = Path(__file__).resolve().parents[2]

PAGES = {
    "read_tax_book": "/studio/tax", "read_estate": "/studio/estate", "read_family": "/studio/family",
    "read_behavioural": "/studio/behavioural", "read_regulatory_countdown": "/studio/regulatory-countdown",
    "read_skills": "/studio/skills", "create_skill": "/studio/skills", "update_skill": "/studio/skills",
    "delete_skill": "/studio/skills", "clone_skill": "/studio/skills", "run_skill": "/studio/skills",
    "read_book_batches": "/studio/book-integration",
}
WRITES = ("create_skill", "update_skill", "delete_skill", "clone_skill", "run_skill", "start_book_batch")


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def nav_roles() -> dict[str, set[str]]:
    nav = (ROOT / "frontend" / "lib" / "nav.ts").read_text(encoding="utf-8")
    alias = {"A": "adviser", "P": "paraplanner", "PT": "portfolio_team", "R": "research_cio",
             "C": "compliance", "O": "operations", "B": "branch_leader"}
    return {m.group(1): {x.strip().strip('"') if x.strip().startswith('"') else alias[x.strip()] for x in m.group(2).split(",") if x.strip()}
            for m in re.finditer(r'\{ href: "([^"]+)",[^\n]*?roles: \[([^\]]*)\]', nav)}


def gating() -> None:
    print("\n=== Held by the roles each page's sidebar entry shows ===")
    nav = nav_roles()
    for key, href in PAGES.items():
        tool = TOOLS[key]
        check(f"{key} is held by the {href} roles and admin", {r.value for r in tool.roles_required}, nav[href] | {"admin"})
        check(f"a client holds no {key}", key in tools_for_role(R.CLIENT), False)
    for key in PAGES:
        if key not in WRITES:
            check(f"{key} changes nothing", TOOLS[key].change_state, ToolChangeState.NO)
    for key in WRITES:
        check(f"{key} stops for a Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    check("only operations loads a book", {r.value for r in TOOLS["start_book_batch"].roles_required}, {"operations"})
    check("an adviser may not read the acquired books", validate_tool_call(R.ADVISER, "read_book_batches", {})[0], False)

    print("\n=== The views open beside the conversation ===")
    panes = (ROOT / "frontend" / "lib" / "paneViews.ts").read_text(encoding="utf-8")
    pane_body = (ROOT / "frontend" / "app" / "workspace" / "components" / "WorkPane.tsx").read_text(encoding="utf-8")
    for view in ("tax", "estate", "family", "behavioural", "regulatory_countdown", "workforce", "skills", "book_integration", "book_batch"):
        check(f"{view} is a view open_view knows", view in OPEN_VIEWS, True)
        check(f"{view} is a pane view", f'key: "{view}"' in panes and f'case "{view}":' in pane_body, True)
    renderer = (ROOT / "frontend" / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
    for key in ("read_tax_book", "read_estate", "read_family", "read_behavioural", "read_regulatory_countdown", "read_skills", "read_book_batches"):
        check(f"{key} draws a card", f'case "{key}":' in renderer, True)
    for page in ("tax", "estate", "family", "behavioural", "regulatory-countdown", "workforce", "skills", "book-integration"):
        src = (ROOT / "frontend" / "app" / "(app)" / "studio" / page / "page.tsx").read_text(encoding="utf-8")
        check(f"the {page} page is the view, not a copy of it", len(src.splitlines()) < 30 and "View" in src, True)


def figures_in_code() -> None:
    """B-120: what the Studio pages state comes from a cited figure, a record, or is named as assumed."""
    from datetime import date

    from app.aurea_core import behavioural, law_figures, regulatory_countdown as rc, tax_intelligence as ti

    today = date(2026, 11, 17)
    print("\n=== Every figure fixed in law carries its source and the day it was taken (B-120) ===")
    for f in law_figures.ALL:
        check(f"{f.key} ({f.applies_from}) cites a source and an as-of date", bool(f.source.strip()) and f.as_of <= today, True)
    check("a figure past its window reports itself expired", law_figures.get("us_tcja_sunset").status(today), "expired")
    check("one still to come is upcoming", law_figures.get("uk_pension_iht").status(today), "upcoming")
    check("one whose date has passed is in force", law_figures.get("uk_bpr_apr_cap").status(today), "in_force")
    check("this year's gift exclusion expires with the year",
          law_figures.get("us_annual_gift_exclusion", today).status(date(2027, 1, 1)), "expired")

    print("\n=== A yearly figure is read on the firm's date, and the figures match their sources (B-197) ===")
    new_year = date(2027, 1, 1)
    check("2026's exclusions on the firm's 2026 date",
          (law_figures.value("us_basic_exclusion", today), law_figures.value("us_annual_gift_exclusion", today)), (15_000_000, 19_000))
    check("and 2027's from its first day (Rev. Proc. 2026-35)",
          (law_figures.value("us_basic_exclusion", new_year), law_figures.value("us_annual_gift_exclusion", new_year)), (15_480_000, 20_000))
    check("past every window it is the latest, reading expired",
          (law_figures.value("us_basic_exclusion", date(2028, 6, 1)), law_figures.get("us_basic_exclusion", date(2028, 6, 1)).status(date(2028, 6, 1))),
          (15_480_000, "expired"))
    try:
        law_figures.value("us_basic_exclusion")
        refused = False
    except ValueError:
        refused = True
    check("a yearly figure read without a date is refused", refused, True)
    check("BPR/APR allowance is £2.5M (Finance Act 2026 Sch 12)", law_figures.value("uk_bpr_apr_cap"), 2_500_000)
    check("the nil-rate bands are frozen to 5 Apr 2031", {law_figures.get(k).applies_to for k in ("uk_nrb", "uk_rnrb", "uk_rnrb_taper")},
          {date(2031, 4, 5)})
    check("UK dividend rates for 2026/27", (ti._UK_DIVIDEND_RATE_BASIC, ti._UK_DIVIDEND_RATE_HIGHER, ti._UK_DIVIDEND_RATE_ADDITIONAL),
          (0.1075, 0.3575, 0.3935))
    check("US brackets and LTCG ceilings are 2026's", (ti._US_BRACKETS_MFJ[0][0], ti._US_BRACKETS_SINGLE[0][0], ti._US_LTCG_RATE_ZERO_MFJ),
          (24_800, 12_400, 98_900))
    check("the countdown's law cites 2027's exclusion once the year turns",
          [l["value"] for l in rc._us_estate_exemption({"totals": {"total_value": 1}, "persons": [], "accounts": [],
                                                       "household": {"values": {}}, "goals": []}, new_year)["law"]
           if l["key"] == "us_basic_exclusion"], [15_480_000])

    couple = {"totals": {"total_value": 40_000_000, "by_asset_class": {"equity": 10_000_000, "alternatives": 5_000_000}},
              "persons": [{"full_name": "A One", "profile": {}}, {"full_name": "B One", "profile": {}},
                          {"full_name": "C One", "is_next_gen": True, "profile": {}}],
              "accounts": [{"total_value": 40_000_000}], "household": {"name": "One", "values": {}}, "goals": []}

    print("\n=== The regulatory countdown (B-120) ===")
    us = rc._us_estate_exemption(couple, today)
    check("the US sunset that passed is expired: no countdown, not critical", (us["status"], us["days_remaining"], us["urgency"]),
          ("expired", 0, "expired"))
    check("the exclusion is P.L. 119-21's, doubled for a couple and named as assumed",
          (us["figures"]["exemption"], any("portability" in a for a in us["assumed"])), (30_000_000, True))
    check("exposure and tax are worked from it", (us["figures"]["exposure"], us["figures"]["estimated_tax"]), (10_000_000, 4_000_000.0))
    check("no gift action assumes four recipients", any("* 4" in a["estimated_benefit"] or "76,000" in a["estimated_benefit"] for a in us["actions"]), False)
    check("no §7520 rate is made up", any("~5%" in a["detail"] for a in us["actions"]), False)
    check("its law lists every figure it used", sorted(l["key"] for l in us["law"]),
          ["us_annual_gift_exclusion", "us_basic_exclusion", "us_estate_tax_rate", "us_tcja_sunset"])
    pension = rc._uk_iht_pension(couple, today)
    check("the UK pension change counts down from the firm's today", (pension["status"], pension["days_remaining"]),
          ("upcoming", (date(2027, 4, 6) - today).days))
    bpr = rc._uk_bpr_apr_cap(couple, today)
    check("BPR/APR in force since 6 Apr 2026, not counted down to 2027", (bpr["status"], bpr["deadline"]), ("in_force", "2026-04-06"))
    check("its qualifying assets are not on file, so not assessed: no alternatives proxy",
          (bpr["figures"]["assessed"], bpr["figures"]["estimated_qualifying_assets"], bpr["figures"]["estimated_additional_iht"]), (False, None, None))
    check("the FCA date nobody sourced is gone", hasattr(rc, "_uk_fca_targeted_support"), False)

    print("\n=== The tax view (B-120) ===")
    gift = ti._us_gift_tracker(couple, today=today)
    check("gifts count only the recipients on file", (gift["recipients_on_file"], gift["max_annual_exclusion_gifts"]), (1, 2 * 1 * 19_000))
    check("and say who is not", gift["not_on_file"], ["recipients outside the household"])
    check("the estate is read against the exclusion in force", gift["estate_exclusion"], 30_000_000)
    div = ti._uk_dividend_allowance(couple)
    check("dividends from the equity on file, not 60% of everything", (div["equity_value"], div["estimated_annual_dividends"]),
          (10_000_000, 250_000.0))
    check("the yield is named as assumed", div["assumed"]["equity_dividend_yield"], 0.025)
    saver = {"persons": [{"full_name": "Kai", "profile": {"tax": {"marginal_rate": 0.33, "kiwisaver_rate": 0.03, "annual_income": 80_000}}}]}
    ks = ti._kiwisaver_optimisation(saver, today=today)["recommendations"][0]
    check("no employer rate is assumed", (ks["employer_match_annual"], ks["not_on_file"]), (None, ["employer contribution rate"]))
    check("the government contribution is Budget 2025's, with its source", (ks["max_govt_topup"], "Taxation (Budget Measures) Act 2025" in ks["law"][1]["source"]),
          (260.72, True))
    saver["persons"][0]["profile"]["tax"]["employer_contribution_rate"] = 0.035
    check("a recorded employer rate is used", ti._kiwisaver_optimisation(saver, today=today)["recommendations"][0]["employer_match_annual"], 2800.0)
    saver["persons"][0]["profile"]["tax"]["annual_income"] = 200_000
    check("above the income limit there is no government contribution",
          ti._kiwisaver_optimisation(saver, today=today)["recommendations"][0]["max_govt_topup"], 0)

    print("\n=== The behavioural draft claims nothing no tool established (B-120) ===")
    stressed = {"is_stressed": True, "drawdown_pct": -12.5}
    for bias in (behavioural.LOSS_AVERSION, behavioural.OVERCONFIDENCE, None):
        body = behavioural._draft_stress_message(couple, stressed, {"dominant_bias": bias})["draft_message"]
        check(f"{bias or 'no bias'}: no claim about the strategy, a stress test or agreed parameters",
              any(w in body for w in ("no changes to the strategy", "stress-tested", "within the parameters", "remains unchanged",
                                      "has not changed", "no adjustments are needed")), False)
        check(f"{bias or 'no bias'}: states the drawdown on file", "12.5% below cost" in body, True)
    undated = {**couple, "goals": [{"kind": "retirement", "assumptions": {}}]}
    check("a goal with no horizon gets none: no 10-year fallback",
          "10-year" in behavioural._draft_stress_message(undated, stressed, {})["draft_message"], False)
    dated = {**couple, "goals": [{"kind": "retirement", "assumptions": {"years": 14}}]}
    check("a recorded horizon is used", "14-year retirement goal" in behavioural._draft_stress_message(dated, stressed, {})["draft_message"], True)


async def behaviour() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            users = {}
            for role, name in ((R.ADVISER, "Ann Adviser"), (R.PARAPLANNER, "Pat Para"), (R.OPERATIONS, "Ola Ops"), (R.ADVISER, "Ben Adviser")):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{name.split()[0].lower()}@t.test", full_name=name,
                         hashed_password="x", role=role)
                s.add(u)
                users[name] = u
            stranger = User(id=uuid.uuid4(), firm_id=other.id, email="zed@o.test", full_name="Zed Other", hashed_password="x", role=R.ADVISER)
            s.add(stranger)
            await s.flush()

            def ex(user, role=None, f=firm):
                return ToolExecutors(s, user.id, role or UserRole(user.role), f.id)

            ann, pat, ola, ben = users["Ann Adviser"], users["Pat Para"], users["Ola Ops"], users["Ben Adviser"]

            # Two households in one family (a parent and a child), and one in another firm.
            lee = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            kim = Household(id=uuid.uuid4(), firm_id=firm.id, name="Kim")
            zed = Household(id=uuid.uuid4(), firm_id=other.id, name="Zed")
            s.add_all([lee, kim, zed])
            await s.flush()
            mum = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=lee.id, full_name="Mia Lee")
            son = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=kim.id, full_name="Sam Kim", is_next_gen=True)
            zoe = Person(id=uuid.uuid4(), firm_id=other.id, household_id=zed.id, full_name="Zoe Zed")
            s.add_all([mum, son, zoe])
            await s.flush()
            s.add(RelationshipEdge(firm_id=firm.id, kind="parent", from_type="person", from_id=mum.id, to_type="person", to_id=son.id))
            # An edge in the other firm pointing at our person is not followed.
            s.add(RelationshipEdge(firm_id=other.id, kind="parent", from_type="person", from_id=zoe.id, to_type="person", to_id=mum.id))
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=mum.id, name="Mia mandate", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            taxable = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Taxable", custodian="C", currency="USD", cash_balance=0)
            ira = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="IRA", custodian="C", currency="USD", cash_balance=0,
                          registration_type="traditional_ira")
            s.add_all([taxable, ira])
            await s.flush()
            for acc, symbol, value, cost in ((taxable, "AAA", 50000, 30000), (ira, "BBB", 40000, 10000)):
                inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=symbol, name=symbol, asset_class="equity",
                                  market_type=MarketType.PUBLIC, currency="USD")
                s.add(inst)
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=today, close=10, currency="USD", source="test", is_real=True))
                s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acc.id, instrument_id=inst.id,
                              quantity=value / 10, market_value=value, cost_basis=cost))
            await s.flush()

            print("\n=== Family reads one firm ===")
            fam = await ex(ann).execute("read_family", {"household_id": str(lee.id)})
            check("the family spans both households", sorted(h["name"] for h in fam["households"]), ["Kim", "Lee"])
            check("it says what its AUM covers", (fam["total_family_aum"], bool(fam["aum_basis"])), (90000.0, True))
            check("another firm's person is not drawn in", "Zoe Zed" in [p["full_name"] for p in fam["persons"]], False)
            for label, kw in (("household", {"household_id": zed.id}), ("person", {"person_id": zoe.id})):
                try:
                    await family.for_anchor(s, firm.id, **kw)
                    check(f"another firm's {label} is not found", False, True)
                except family.FamilyError:
                    check(f"another firm's {label} is not found", True, True)
            try:
                await ex(ann).execute("read_family", {"household_id": str(zed.id)})
                check("nor through the tool", False, True)
            except ExecutorError:
                check("nor through the tool", True, True)

            print("\n=== Estate steps basis up only where the market says, never in a retirement account ===")
            est = await ex(ann).execute("read_estate", {"household_id": str(lee.id)})
            step = est["step_up_basis"]
            check("a US household: the taxable gain only", (step["market"], step["unrealised_gain"], step["retirement_value_excluded"]),
                  ("US", 20000.0, 40000.0))
            check("in the household's currency", est["currency"], "USD")
            lee.values = {"jurisdiction": "NZ"}
            await s.flush()
            check("a household on New Zealand's rules: no step-up", (await estate.for_household(s, lee.id, firm_id=firm.id))["step_up_basis"], None)
            lee.values = {}
            await s.flush()

            print("\n=== The other reads ===")
            beh = await ex(ann).execute("read_behavioural", {"household_id": str(lee.id)})
            check("behavioural answers for the household", beh["household_name"], "Lee")
            reg = await ex(ann).execute("read_regulatory_countdown", {})
            check("the regulatory countdown, firm-wide", (isinstance(reg["results"], list), reg["total"] == len(reg["results"])), (True, True))
            one = await ex(ann).execute("read_regulatory_countdown", {"household_id": str(lee.id)})
            check("and for one household on its market", one["jurisdiction"], "US")
            us = one["analyses"][0]
            check("B-120: the passed sunset is expired, not a countdown at zero and critical",
                  (us["status"], us["days_remaining"], us["urgency"]), ("expired", 0, "expired"))
            check("B-120: its exclusion is the one in force, with its source",
                  (us["figures"]["exemption_per_person"], any("119-21" in l["source"] for l in us["law"])), (15_000_000, True))
            check("B-120: no post-sunset guess survives", "post_sunset_exemption" in us["figures"], False)
            book = await ex(ann).execute("read_tax_book", {})
            check("the tax book scans the firm's households", book["total_households_scanned"], 2)

            print("\n=== Skills ===")
            try:
                await ex(ann).preview_change("create_skill", {"name": "Big", "instruction": "Do it", "default_tier": "tier_3"})
                check("a skill above Tier 2 is refused", False, True)
            except ExecutorError as exc:
                check("a skill above Tier 2 is refused", "Tier 2" in str(exc), True)
            rows = await ex(ann).preview_change("create_skill", {"name": "Cash drag", "instruction": "Flag cash over 10%",
                                                                 "visibility": "shared", "shared_with": ["Pat Para"]})
            check("its Confirm card names it and who sees it", {r["label"]: r["value"] for r in rows["rows"]}.get("Visible to"), "You and Pat Para")
            made = await ex(ann).execute("create_skill", {"name": "Cash drag", "instruction": "Flag cash over 10%",
                                                          "visibility": "shared", "shared_with": ["Pat Para"]})
            sid = made["skill"]["id"]
            check("created, shared with the colleague named", made["skill"]["shared_with"], [str(pat.id)])
            check("the colleague sees it", [x["name"] for x in (await ex(pat).execute("read_skills", {}))["skills"]], ["Cash drag"])
            check("someone it is not shared with does not", (await ex(ben).execute("read_skills", {}))["skills"], [])
            for who, key, inputs in ((pat, "update_skill", {"skill_id": sid, "name": "Mine now"}), (pat, "delete_skill", {"skill_id": sid})):
                try:
                    await ex(who).preview_change(key, inputs)
                    check(f"{key} by someone else is refused", False, True)
                except ExecutorError as exc:
                    check(f"{key} by someone else is refused", "owner" in str(exc), True)
            copy = await ex(pat).execute("clone_skill", {"skill_id": sid})
            check("a copy is private and theirs", (copy["skill"]["visibility"], copy["skill"]["mine"]), ("private", True))
            try:
                await ex(ben).execute("clone_skill", {"skill_id": sid})
                check("a skill you cannot see cannot be copied", False, True)
            except ExecutorError:
                check("a skill you cannot see cannot be copied", True, True)
            changed = await ex(ann).execute("update_skill", {"skill_id": sid, "enabled": "false"})
            check("its owner disables it", changed["skill"]["enabled"], False)
            try:
                await ex(ann).preview_change("run_skill", {"skill_id": sid})
                check("a disabled skill does not run", False, True)
            except ExecutorError as exc:
                check("a disabled skill does not run", "disabled" in str(exc), True)
            await ex(ann).execute("update_skill", {"skill_id": sid, "enabled": "true"})
            run_rows = {r["label"]: r["value"] for r in (await ex(ann).preview_change("run_skill", {"skill_id": sid}))["rows"]}
            check("its run's Confirm card says it only proposes", ("Proposes" in run_rows["What it does"], run_rows["Runs over"]),
                  (True, "Every client (2 households)"))

            from app.skills import runtime as skill_runtime

            async def fake_ask(session, f, skill, brain):
                return {"applies": brain["household"]["name"] == "Lee", "title": "Cash above 10%", "summary": "", "rationale": ""}
            real_ask, skill_runtime._ask = skill_runtime._ask, fake_ask
            try:
                ran = await ex(ann).execute("run_skill", {"skill_id": sid})
            finally:
                skill_runtime._ask = real_ask
            check("a run looks at every client and proposes for the one it matches", (ran["scanned"], ran["surfaced"],
                  [p["household"] for p in ran["proposals"]]), (2, 1, ["Lee"]))
            rec = await s.get(Recommendation, uuid.UUID(ran["proposals"][0]["recommendation_id"]))
            check("as a proposal for a person, at Tier 2 or below", (str(getattr(rec.status, "value", rec.status)), str(rec.tier) in ("tier_1", "tier_2", "AutonomyTier.TIER_1", "AutonomyTier.TIER_2")),
                  ("proposed", True))
            gone = await ex(ann).execute("delete_skill", {"skill_id": sid})
            check("its owner deletes it", ((await s.get(Skill, uuid.UUID(sid))) is None, gone["deleted"]["name"]), (True, "Cash drag"))

            print("\n=== Book integration ===")
            preview = {r["label"]: r["value"] for r in (await ex(ola).preview_change("start_book_batch", {"source_firm": "Northbridge"}))["rows"]}
            check("its Confirm card says the feed is the sample", "Sample" in preview["Feed"], True)
            started = await ex(ola).execute("start_book_batch", {"source_firm": "Northbridge"})
            bid = started["batch"]["id"]
            check("the batch is marked as the sample it is", started["batch"]["sample"], True)
            listed = await ex(ola).execute("read_book_batches", {})
            check("it is listed", [b["id"] for b in listed["batches"]], [bid])
            one_b = await ex(ola).execute("read_book_batches", {"batch_id": bid})
            check("one batch says it is a sample", "sample book" in one_b["message"], True)
            resolved = await ex(ola)._resolve_run({"agent_key": "book_integration", "batch_id": bid})
            check("operations may run book integration on it", (resolved["subject"].type, str(resolved["subject"].id)), ("book_batch", bid))
            for label, who, inputs, word in (
                ("an adviser may not", ann, {"agent_key": "book_integration", "batch_id": bid}, "role"),
                ("it needs a batch", ola, {"agent_key": "book_integration"}, "batch_id"),
            ):
                try:
                    await ex(who)._resolve_run(inputs)
                    check(label, False, True)
                except ExecutorError as exc:
                    check(label, word in str(exc), True)
            batch = await book_batches.get(s, firm.id, uuid.UUID(bid))
            batch.status = "reconciled"
            await s.flush()
            try:
                await ex(ola)._resolve_run({"agent_key": "book_integration", "batch_id": bid})
                check("a reconciled book is not run again", False, True)
            except ExecutorError as exc:
                check("a reconciled book is not run again", "already" in str(exc), True)
            try:
                await ex(stranger, R.OPERATIONS, other)._resolve_run({"agent_key": "book_integration", "batch_id": bid})
                check("another firm's batch is not found", False, True)
            except ExecutorError as exc:
                check("another firm's batch is not found", "not found" in str(exc), True)

            print("\n=== open_view grounds a batch ===")
            turn = [{"result": one_b}]
            opened = await ex(ola).open_view({"view": "book_batch", "batch_id": bid}, turn=turn)
            check("a batch read this turn opens", opened["view"], f"/studio/book-integration/{bid}")
            try:
                await ex(ola).open_view({"view": "book_batch", "batch_id": bid}, turn=[])
                check("one from memory is refused", False, True)
            except ExecutorError:
                check("one from memory is refused", True, True)
            est_view = await ex(ann).open_view({"view": "estate", "household_id": str(lee.id)}, turn=[{"result": est}])
            check("estate opens on one client", est_view["view"], f"/studio/estate?household={lee.id}")
    finally:
        await db.dispose()


def main() -> int:
    gating()
    figures_in_code()
    asyncio.run(behaviour())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
