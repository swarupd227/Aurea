"""A read-only proof, on the live database, that the India work reads what the demonstration firm was seeded with.

Run inside the backend container (or anywhere the app's own database settings resolve), from /app:

    python -m app.tools_live.india_live_proof

It connects with the app's own session factory, finds the Indian demonstration firm (slug `india-demo`) and exercises each
India feature's READ path through the code the conversation and the API use (the ToolExecutors `read_*` tools, gated by
the same role check the gateway applies, and the `aurea_core` read functions beneath them), as the roles that hold them.
For each check it prints one line, PASS or FAIL, with the feature, the role and what was observed, then a summary. It
exits non-zero on any FAIL.

IT WRITES NOTHING, and three things keep it so (see `install_guards`):
  * the session is only ever rolled back; `commit`, `add`, `add_all` and `delete` on it raise;
  * a `before_flush` listener raises if anything is new, deleted or modified;
  * a `before_cursor_execute` listener raises on any statement that is not a SELECT, a WITH query, SHOW, PRAGMA or EXPLAIN,
    and on PostgreSQL the transaction is `SET TRANSACTION READ ONLY` so the server refuses a write as well.
`guard_selftest` proves the guards on a throwaway in-memory SQLite database, never on the live one. A read tool that
writes is not run: a tool that trips a guard is reported as a FAIL naming the write, and the guard has already stopped it.

Not exercised here, because each writes: every `record_*`, `prepare_*` followed by a Confirm, `raise_india_reminders` and the
reminder job, `decide_recommendation`, consents, PANs, SIPs, orders and approvals. The suites in backend/tests prove those.

To add a feature, write an `async def` taking the `Proof` and decorate it with `@group`; it is run in file order.
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from contextlib import asynccontextmanager

from sqlalchemy import event, select, text

SLUG = "india-demo"
STAFF_ROLES = ("adviser", "paraplanner", "portfolio_team", "compliance", "operations", "branch_leader", "admin")
INSTITUTIONAL = {"research_cio"}

# The India tools that read; a staff role runs the ones the registry gives it. `{"household": ...}` is filled in per firm.
INDIA_READ_TOOLS = {
    "read_consolidation": {},
    "read_licences": {}, "read_pre_advice": {}, "read_compliance_calendar": {}, "read_residency": {}, "read_pms": {},
    "read_india_tax_records": {}, "read_kyc": {}, "read_aggregator": {}, "read_complaints": {}, "read_sebi_family": {},
    "read_india_estate": {}, "read_governance": {}, "read_india_reminders": {}, "read_accredited_investors": {},
    "read_adviser_fee_terms": {}, "read_rate_card": {}, "read_lrs": {}, "read_sip_mandates": {}, "read_india_savings": {},
    "read_pledges": {}, "read_client_channels": {}, "read_india_stack": {}, "read_mf_orders": {},
    "read_distribution_separation": {}, "read_adviser_certifications": {}, "read_ad_approvals": {}, "read_billing": {},
    "read_ticket_gates": {"household": "The Iyer Family"}, "read_tax_habits": {"household": "The Sharma Family"},
    "check_advertising": {"text": "Investments in the securities market are subject to market risks."},
    "read_mf_figures": {}, "read_agreement_contents": {"household": "The Sharma Family"},
    "read_health_cover": {"household": "The Sharma Family"}, "read_policy_values": {"household": "The Sharma Family"},
    "read_retirement_stack": {"household": "The Sharma Family"},
    "read_after_tax_comparison": {"household": "The Sharma Family"}, "read_tax_reviews": {},
    "read_advice_audit": {},
    "read_agreement_file": {"household": "The Sharma Family"}, "read_fund_acceptance": {},
    "read_estate_register": {"household": "The Sharma Family"}, "read_nri_planning": {"household": "The Menon NRI Household"},
    "read_planning_measures": {"household": "The Mehta Family"},
    "read_manager_staff": {}, "read_pms_ledger": {}, "read_guardian_papers": {}, "read_kyc_review_period": {},
    "read_real_estate_choice": {"acquired_on": "2015-04-01", "sold_on": "2026-06-01", "cost": 5000000, "proceeds": 12000000, "resident_individual_or_huf": "yes"},
}
# Tools only compliance, a branch leader, operations or the administrator hold (the registers are theirs).
OVERSIGHT_ONLY = ("read_complaints", "read_governance", "read_rate_card", "read_compliance_calendar")


class WriteBlocked(RuntimeError):
    """Raised by a guard when anything tries to write."""


# --------------------------------------------------------------------------------------------------------------------
# The guards
# --------------------------------------------------------------------------------------------------------------------

_READ_HEADS = ("select", "show", "pragma", "explain")
_WRITE_WORDS = re.compile(r"\b(insert|update|delete|merge|truncate|alter|drop|create|grant|revoke)\b")


def _statement_guard(conn, cursor, statement, parameters, context, executemany):
    head = " ".join(statement.strip().lower().split())
    if head == "set transaction read only" or head.startswith(_READ_HEADS):
        return
    if head.startswith("with") and not _WRITE_WORDS.search(head):
        return
    raise WriteBlocked(f"a write reached the cursor and was stopped: {head[:80]}")


def _flush_guard(session, flush_context, instances):
    changed = list(session.new) + list(session.deleted) + [o for o in session.dirty if session.is_modified(o)]
    if changed:
        names = sorted({type(o).__name__ for o in changed})
        raise WriteBlocked(f"a flush with {len(changed)} new, deleted or changed object(s) was stopped: {', '.join(names)}")


def install_guards(session):
    """Make `session` unable to write. Returns a function that takes the guards off again."""
    sync = session.sync_session
    engine = sync.get_bind()
    event.listen(sync, "before_flush", _flush_guard)
    event.listen(engine, "before_cursor_execute", _statement_guard)
    saved = {n: getattr(session, n) for n in ("commit", "add", "add_all", "delete")}

    def _refuse(what):
        def _raise(*a, **k):
            raise WriteBlocked(f"{what} is not allowed in a read-only proof")
        return _raise

    async def _no_commit(*a, **k):
        raise WriteBlocked("commit is not allowed in a read-only proof")

    async def _no_delete(*a, **k):
        raise WriteBlocked("delete is not allowed in a read-only proof")

    session.commit = _no_commit
    session.add = _refuse("add")
    session.add_all = _refuse("add_all")
    session.delete = _no_delete

    def remove():
        event.remove(sync, "before_flush", _flush_guard)
        event.remove(engine, "before_cursor_execute", _statement_guard)
        for n, f in saved.items():
            setattr(session, n, f)

    return remove


async def make_read_only(session) -> None:
    """On PostgreSQL the server itself refuses a write for the rest of this transaction."""
    if session.sync_session.get_bind().dialect.name == "postgresql":
        await session.execute(text("SET TRANSACTION READ ONLY"))


async def guard_selftest() -> list[tuple[bool, str]]:
    """Prove the guards on a throwaway in-memory database, so a failing guard cannot touch the live one."""
    from sqlalchemy import Column, Integer
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

    class Probe(Base):
        __tablename__ = "probe"
        id = Column(Integer, primary_key=True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    results: list[tuple[bool, str]] = []
    async with async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)() as s:
        remove = install_guards(s)

        async def blocked(label, coro):
            try:
                await coro
                results.append((False, f"{label}: NOT stopped"))
            except WriteBlocked as exc:
                results.append((True, f"{label}: stopped ({str(exc)[:60]})"))
            await s.rollback()

        async def _add():
            s.add(Probe(id=1))

        await blocked("session.add", _add())
        s.sync_session.add(Probe(id=2))            # past the add() refusal, to prove before_flush on its own
        await blocked("a flush with a new row", s.flush())
        await blocked("a raw INSERT", s.execute(text("insert into probe (id) values (3)")))
        await blocked("session.commit", s.commit())
        await blocked("a raw DELETE", s.execute(text("delete from probe")))
        try:
            n = (await s.execute(text("select count(*) from probe"))).scalar_one()
            results.append((n == 0, f"a SELECT still runs and nothing was written (rows: {n})"))
        except Exception as exc:       # noqa: BLE001
            results.append((False, f"a SELECT failed: {exc!r}"))
        remove()
    await engine.dispose()
    return results


# --------------------------------------------------------------------------------------------------------------------
# The proof
# --------------------------------------------------------------------------------------------------------------------

def trim(x, n: int = 150) -> str:
    s = str(x)
    return s if len(s) <= n else s[: n - 1] + "…"


class Proof:
    def __init__(self, session, out=print):
        self.session = session
        self.out = out
        self.passed = 0
        self.failed = 0
        self.infos: list[str] = []
        self.firm = None
        self.users: dict = {}
        self.houses: dict = {}
        self.people: dict = {}
        self.today = None

    # -- reporting -----------------------------------------------------------------------------------------------
    def ok(self, feature: str, role: str, cond: bool, observed) -> bool:
        cond = bool(cond)
        self.passed += cond
        self.failed += not cond
        self.out(f"{'PASS' if cond else 'FAIL'}  {feature} | {role} | {trim(observed)}")
        return cond

    def info(self, feature: str, role: str, observed) -> None:
        line = f"INFO  {feature} | {role} | {trim(observed)}"
        self.infos.append(line)
        self.out(line)

    @asynccontextmanager
    async def section(self, feature: str, role: str):
        try:
            yield
        except WriteBlocked as exc:
            self.ok(feature, role, False, f"A READ WROTE: {exc}")
            await self.reset()
        except Exception as exc:      # noqa: BLE001
            self.ok(feature, role, False, f"raised {type(exc).__name__}: {trim(exc, 120)}")
            await self.reset()

    async def reset(self) -> None:
        await self.session.rollback()
        await make_read_only(self.session)

    # -- the app's own gate and executors ------------------------------------------------------------------------
    async def tool(self, role: str, key: str, inputs: dict | None = None) -> dict:
        from app.core.tools import validate_tool_call
        from app.llm.executors import ToolExecutors
        from app.models.enums import UserRole

        inputs = dict(inputs or {})
        allowed, why = validate_tool_call(UserRole(role), key, inputs)
        if not allowed:
            raise PermissionError(why)
        user = self.users[role]
        return await ToolExecutors(self.session, user.id, UserRole(role), self.firm.id).execute(key, inputs)

    def inputs_for(self, key: str) -> dict:
        spec = dict(INDIA_READ_TOOLS.get(key, {}))
        if "household" in spec:
            spec["household_id"] = str(self.houses[spec.pop("household")].id)
        return spec


GROUPS = []


def group(fn):
    GROUPS.append(fn)
    return fn


def pick(rows, **want):
    return next((r for r in rows if all(r.get(k) == v for k, v in want.items())), None)


# -- the firm and the guards --------------------------------------------------------------------------------------

@group
async def g_firm(P: Proof):
    from app.aurea_core import licences
    from app.compliance import ontology

    f = P.firm
    P.ok("The firm", "-", (f.jurisdiction, f.base_currency) == ("IN", "INR") and ontology.framework_for(f).regime == "IN-SEBI",
         f"{f.name}: {f.jurisdiction} {f.base_currency} {ontology.framework_for(f).regime}")
    P.ok("The firm is a demonstration", "-", (f.settings or {}).get("demonstration") is True, "settings.demonstration")
    P.ok("The firm holds both licences", "-", [l.key for l in licences.held(f)] == ["investment_adviser", "pms_discretionary"],
         [l.key for l in licences.held(f)])
    missing = [r for r in (*STAFF_ROLES, "client") if r not in P.users]
    P.ok("A sign-in per persona", "-", not missing, f"missing: {missing}" if missing else f"{len(P.users)} roles have a user")


@group
async def g_role_boundaries(P: Proof):
    """What each role may read, from the registry the gateway enforces (core/tools.py), and the one tool that filters by role."""
    from app.core.tools import CLIENT_TOOLS, tools_for_role, validate_tool_call
    from app.models.enums import UserRole

    held = {r: set(tools_for_role(UserRole(r))) for r in (*STAFF_ROLES, "client")}
    india_staff = set(INDIA_READ_TOOLS)
    leak = sorted(india_staff & held["client"])
    P.ok("A client cannot read India staff tools", "client", not leak, f"holds none of {len(india_staff)}" if not leak else f"holds {leak}")
    allowed, why = validate_tool_call(UserRole.CLIENT, "read_licences", {})
    P.ok("The gateway refuses a client the licences tool", "client", not allowed, trim(why, 100))
    P.ok("A client's tools are all read-only 'my' tools", "client", all(k.startswith("read_my_") or k in CLIENT_TOOLS for k in held["client"]),
         f"{len(held['client'])} tools")
    for role in ("adviser", "paraplanner", "portfolio_team"):
        got = sorted(set(OVERSIGHT_ONLY) & held[role])
        P.ok("The registers are oversight's, not an adviser's", role, not got, "holds none of the registers" if not got else f"holds {got}")
    for role in ("compliance", "branch_leader", "operations", "admin"):
        got = sorted(set(OVERSIGHT_ONLY) - held[role])
        P.ok("Oversight roles read the complaints register, calendar, governance and rate card", role, not got,
             "holds all four" if not got else f"lacks {got}")
    P.ok("Research reads no licence or pre-advice page", "research_cio",
         not ({"read_licences", "read_pre_advice"} & set(tools_for_role(UserRole.RESEARCH_CIO))), "neither tool")

    async with P.section("read_licences shows governance to its readers only", "adviser vs compliance"):
        a = await P.tool("adviser", "read_licences")
        c = await P.tool("compliance", "read_licences")
        P.ok("Compliance reads the governance register and licence map that an adviser cannot", "adviser vs compliance",
             "governance" not in a and "governance" in c, f"adviser has governance: {'governance' in a}; compliance has: {'governance' in c}")


@group
async def g_decision_rights(P: Proof):
    from app.core import decision_rights as dr
    from app.models.enums import AgentKey, UserRole

    approve = dr.roles_for(AgentKey.DRIFT_REBALANCING, "approve")
    dismiss = dr.roles_for(AgentKey.DRIFT_REBALANCING, "dismiss")
    P.ok("Approving a rebalance is an adviser's or the portfolio team's", "adviser, portfolio_team",
         {UserRole.ADVISER, UserRole.PORTFOLIO_TEAM} <= approve, sorted(r.value for r in approve))
    P.ok("Compliance and the administrator approve no book-moving proposal", "compliance, admin",
         UserRole.COMPLIANCE not in approve and UserRole.ADMIN not in approve, f"compliance: {UserRole.COMPLIANCE in approve}, admin: {UserRole.ADMIN in approve}")
    P.ok("Compliance may dismiss or roll one back", "compliance", UserRole.COMPLIANCE in dismiss, sorted(r.value for r in dismiss))


# -- licences, the gate, the fee cap, the calendar -----------------------------------------------------------------

@group
async def g_licences(P: Proof):
    from app.aurea_core import licences

    async with P.section("Licences page", "compliance"):
        r = await P.tool("compliance", "read_licences")
        keys = sorted(l["key"] for l in r["licences"])
        P.ok("Both licences are held, marked as a demonstration", "compliance", r["applies"] and r["demonstration"] and keys == ["investment_adviser", "pms_discretionary"], keys)
        P.ok("No Indian licence reaches Tier 3", "compliance", r["ceiling"] in ("tier_1", "tier_2"), f"ceiling {r['ceiling']}")
        tracks = {m["licence"] for m in r["mandates"]}
        P.ok("Mandates sit on both tracks", "compliance", tracks == {"investment_adviser", "pms_discretionary"}, f"{len(r['mandates'])} mandates on {sorted(tracks)}")
        gov = r.get("governance") or {}
        lm = {l["key"]: l for l in (gov.get("map") or {}).get("licences", [])}
        P.ok("The licence map counts mandates per licence", "compliance",
             lm.get("investment_adviser", {}).get("mandates", 0) >= 7 and lm.get("pms_discretionary", {}).get("mandates", 0) >= 2,
             {k: v.get("mandates") for k, v in lm.items() if v.get("held")})
        for section in ("pre_advice", "fee_caps", "calendar", "residency", "guardians", "pms", "aggregator", "kyc", "reminders", "family",
                        "estate", "counsel", "ticket_gates", "accredited", "fee_terms", "rate_card", "channels", "india_stack", "lrs",
                        "sip_mandates", "savings", "pledges", "mf_orders", "separation", "ad_approvals",
                        "manager_staff", "pms_ledger", "guardian_papers", "kyc_review", "gain_harvest"):
            if section not in r:
                P.ok("The licences page carries its section", "compliance", False, f"missing {section}")
        P.ok("The licences page carries all thirty India sections", "compliance",
             all(s in r for s in ("pre_advice", "fee_caps", "calendar", "kyc", "lrs", "ad_approvals")), f"{len(r)} keys")
    async with P.section("Licences page", "adviser"):
        r = await P.tool("adviser", "read_licences")
        P.ok("An adviser reads the page without the governance register", "adviser", r["applies"] and "governance" not in r, f"keys {len(r)}")


@group
async def g_pre_advice(P: Proof):
    async with P.section("Nothing advised before the paperwork", "adviser"):
        r = await P.tool("adviser", "read_pre_advice")
        gate = {h["household"]: h for h in r["households"]}
        P.ok("The adviser's six households are gated", "adviser", sorted(gate) == ["The Banerjee Household", "The D'Souza Household", "The Mehta Family", "The Menon NRI Household",
                                                                             "The Sameer Mehta Household", "The Sharma Family"], sorted(gate))
        sh = gate.get("The Sharma Family", {})
        P.ok("The Sharmas have all five on file", "adviser", sh.get("met") is True and [c["state"] for c in sh["checks"] if c["key"] != "family"] == ["pass"] * 5,
             [c["state"] for c in sh.get("checks", [])])
        ds = gate.get("The D'Souza Household", {})
        P.ok("The D'Souzas hold nothing outside SEBI's purview, so no declaration is asked", "adviser", ds.get("met") is True and ds["checks"][-1]["state"] == "n/a", ds["checks"][-1])
        P.ok("The Banerjees have not consented to their risk profile", "adviser", gate["The Banerjee Household"]["missing"] == ["Risk profile complete and consented"], gate["The Banerjee Household"]["missing"])
        P.ok("The Menons lack only Anita's FATCA and CRS self-certification (No advice yet)", "adviser", gate["The Menon NRI Household"]["missing"] == ["FATCA and CRS self-certification"], gate["The Menon NRI Household"]["missing"])
    async with P.section("The pre-advice gate for one household", "paraplanner"):
        r = await P.tool("paraplanner", "read_pre_advice", {"household_id": str(P.houses["The Menon NRI Household"].id)})
        P.ok("A paraplanner reads the same gate for one household", "paraplanner", "FATCA and CRS self-certification" in str(r), trim(r.get("message") or list(r)))


@group
async def g_fee_cap(P: Proof):
    from app.aurea_core import billing, law_figures

    async with P.section("Fees against SEBI's cap", "adviser"):
        P.ok("The caps carry their circular", "adviser", all("para 1(iii)" in f["source"] for f in law_figures.cited(["in_ia_fee_fixed_cap", "in_ia_fee_aua_cap"], P.today)), "para 1(iii)")
        pv = await billing.preview(P.session, P.firm.id)
        bills: dict[str, list] = {}
        for e in pv["arrangements"]:
            bills.setdefault(e["household_name"], []).append(e)
        d = bills["The D'Souza Household"][0]
        P.ok("A household blocked at the fee cap: a fixed fee above Rs 1,51,000 is held", "adviser",
             d["blocked"] and d["fee_cap"]["cap"] == 151000.0 and d["fee_cap"]["basis"] == "fixed-fee mode" and any(c["code"] == "sebi_fee_cap" for c in d["checks"]),
             f"blocked {d['blocked']}, cap {d['fee_cap']['cap']}, {d['fee_cap']['basis']}")
        s = bills["The Sharma Family"]
        P.ok("1% of assets is within 2.5% of the family's securities, the HUF's included", "adviser",
             len(s) == 3 and not any(c["code"] == "sebi_fee_cap" for e in s for c in e["checks"]) and not s[0]["fee_cap"]["over"] and s[0]["fee_cap"]["basis"].startswith("AUA mode"),
             f"{len(s)} arrangements, {s[0]['fee_cap']['basis']}")
        P.ok("AUA is the securities alone, cash left out", "adviser", s[0]["fee_cap"]["aua"] < sum(e["billable_value"] for e in s), s[0]["fee_cap"]["aua"])
    async with P.section("Billing exceptions", "operations"):
        r = await P.tool("operations", "read_billing")
        P.ok("Operations read billing: mandates with no fee agreement are listed", "operations", "no_agreement" in r.get("exceptions", {}), list(r.get("exceptions", {})))


@group
async def g_calendar(P: Proof):
    from app.aurea_core import compliance_calendar

    async with P.section("The compliance calendar", "compliance"):
        r = await P.tool("compliance", "read_compliance_calendar")
        items = r["items"]
        cm = [i for i in items if i["obligation"] == "complaints_monthly"]
        P.ok("The complaints filing reads the register", "compliance", bool(cm) and all((i.get("register") or {}).get("from_register") for i in cm[:1]), cm[0].get("register") if cm else None)
        P.ok("The portfolio manager's monthly report is on the calendar, with its circular", "compliance", any(i["obligation"] == "pms_monthly_report" and i["citation"] for i in items), "pms_monthly_report")
        P.ok("The next of each obligation is listed once", "compliance", sum(1 for i in items if i["obligation"] == "deposit_revision") == 1, "deposit_revision x1")
        sa = next((i for i in items if i["obligation"] == "saas_undertaking"), None)
        P.ok("A day the circular does not give is named as assumed", "compliance", bool(sa) and sa["due_basis"].startswith("assumed"), sa and sa["due_basis"])
        P.ok("Officers not recorded say so", "compliance", {"Designated Director", "Chief Information Security Officer"} <= set(r["officers_missing"]), r["officers_missing"])
    async with P.section("The compliance calendar", "branch_leader"):
        r = await P.tool("branch_leader", "read_compliance_calendar")
        P.ok("A branch leader reads it too", "branch_leader", r["applies"], f"{len(r['items'])} items")


@group
async def g_residency_guardians(P: Proof):
    from app.aurea_core import guardianship

    async with P.section("Residency", "adviser"):
        r = await P.tool("adviser", "read_residency")
        h = pick(r["households"], household="The Menon NRI Household")
        P.ok("Two Menons are on file, both non-resident", "adviser", bool(h) and h["non_resident"] == 2, h and h["non_resident"])
    async with P.section("Guardianship", "adviser"):
        o = await guardianship.overview(P.session, P.firm)
        st = {m["name"]: m["state"] for m in o["minors"]}
        P.ok("Ira has her father as guardian and Kabir has none", "adviser", (st.get("Ira Menon"), st.get("Kabir Menon")) == ("guardian_on_file", "no_guardian"), st)
        P.ok("Tara Menon has reached majority and her account waits for a person to record the conversion", "adviser", o["attained_majority"] == ["Tara Menon"], o["attained_majority"])


@group
async def g_pms(P: Proof):
    async with P.section("Portfolio Manager clients", "portfolio_team"):
        r = await P.tool("portfolio_team", "read_pms")
        cl = {c["client"]: c for c in r["clients"]}
        k = cl.get("Karthik Iyer", {})
        P.ok("Karthik's agreement passes every check", "portfolio_team", bool(k) and {c["state"] for c in k["checks"]} == {"pass"}, {c["state"] for c in k.get("checks", [])})
        t = cl.get("Kapoor Family Trust", {})
        P.ok("The Kapoor Trust has no agreement recorded and is not assessed", "portfolio_team", bool(t) and t["agreement"] is None and t["not_assessed"] == ["Agreement on file"], t.get("not_assessed"))


@group
async def g_aggregator_complaints(P: Proof):
    from app.aurea_core import held_away

    async with P.section("Account aggregator and consolidated statements", "paraplanner"):
        r = await P.tool("paraplanner", "read_aggregator")
        st = r["statements"]
        P.ok("One CAMS and KFintech statement for the Iyers, two lines, from a document", "paraplanner",
             [(x["household"], x["kind"], x["lines"]) for x in st if x["household"] == "The Iyer Family"] == [("The Iyer Family", "cams_kfintech", 2)], [(x["household"], x["kind"], x["lines"]) for x in st])
        P.ok("No aggregator consent is written by the seed: it is opt-in", "paraplanner", r["consents"] == [] or all(c.get("state") for c in r["consents"]), f"{len(r['consents'])} consent(s)")
        held = await held_away.for_household(P.session, P.houses["The Iyer Family"].id, firm_id=P.firm.id)
        P.ok("Its lines are held-away lines in rupees, resolved to the firm's funds by ISIN", "paraplanner",
             {"HDFC-CORPBOND-REGULAR", "UTI-NIFTY50-REGULAR"} <= {h["symbol"] for h in held["holdings"]},
             # A savings or pension account is a held-away line with no symbol (B-395): it is not one of the funds.
             sorted(h["symbol"] for h in held["holdings"] if h["symbol"]))
    for role in ("compliance", "operations"):
        async with P.section("The complaints register", role):
            r = await P.tool(role, "read_complaints")
            c = r["counts"]
            P.ok("The register carries every state", role, c["resolved"] == 2 and c["withdrawn"] == 1 and c["overdue"] >= 1 and c["total"] >= 7, c)
            imp = [x["ack_state"] for x in r["complaints"] if x["category"] == "impersonation"]
            P.ok("The unacknowledged complaint about impersonation is open with acknowledgement due (or late since)", role, imp and imp[0] in ("ack_due", "ack_overdue"), imp)
            P.ok("A complaint about advice was told to compliance", role, [x["told_compliance"] for x in r["complaints"] if x["category"] == "advice"] == [True], "told_compliance")


@group
async def g_advertising(P: Proof):
    async with P.section("Advertising check on a text (pure)", "adviser"):
        bad = await P.tool("adviser", "check_advertising", {"text": "We guarantee 20% returns every year, risk free. Best adviser in India."})
        good = await P.tool("adviser", "check_advertising", {"text": "Investments in the securities market are subject to market risks. Read all related documents carefully."})
        P.ok("A promise of return is blocked and a stated past return is flagged", "adviser", bad["counts"]["block"] >= 1 and bad["counts"]["flag"] >= 1, bad["counts"])
        P.ok("A plain caveat has no finding, and the check says that is not an approval", "adviser", good["counts"]["block"] == 0 and "not approved" in good["note"], good["note"][:90])


# -- tax, KYC, family, estate ---------------------------------------------------------------------------------------

async def _huf_lot(P: Proof, huf: dict) -> None:
    """The HUF's RELIANCE queue, read for its age today: "turning long-term" shows exactly when the lot at the front is at a loss and
    goes long-term within the window. A lot that has passed twelve months, or stands at a gain, must not show it."""
    from app.aurea_core import tax_india
    from app.aurea_core.valuation import latest_prices
    from app.models.graph import Account
    from app.models.portfolio import Holding, Instrument, TaxLot

    row = (await P.session.execute(
        select(Holding, Instrument).join(Instrument, Instrument.id == Holding.instrument_id).join(Account, Account.id == Holding.account_id)
        .where(Account.firm_id == P.firm.id, Account.name == "Rajesh Sharma HUF demat", Instrument.symbol == "RELIANCE.NS"))).first()
    shown = any(o["kind"] == "turning_long" and o["symbol"] == "RELIANCE.NS" for o in huf["opportunities"])
    lot = None
    if row is not None:
        lot = (await P.session.execute(select(TaxLot).where(TaxLot.holding_id == row[0].id, TaxLot.quantity > 0)
                                       .order_by(TaxLot.acquired_on, TaxLot.created_at))).scalars().first()
    if lot is None:
        P.ok("The HUF's RELIANCE lot is not held, so nothing is turning long-term", "adviser", not shown, f"shown: {shown}")
        return
    turns = tax_india.long_term_from(tax_india.tax_class_of(row[1]), lot.acquired_on)
    price = (await latest_prices(P.session, P.firm.id)).get(row[1].id)
    at_loss = price is not None and float(price.close) < float(lot.cost_per_unit)
    days = (turns - P.today).days
    expected = at_loss and 0 <= days <= tax_india.TURNING_LONG_DAYS
    state = "long-term" if days <= 0 else "turning long-term" if days <= tax_india.TURNING_LONG_DAYS else "short-term"
    P.ok("The HUF's RELIANCE lot reads as its age says: a loss about to go long-term inside the window, and not outside it", "adviser",
         shown == expected, f"acquired {lot.acquired_on}, {state}, {'at a loss' if at_loss else 'not at a loss'}; shown: {shown}"
         + ("" if expected else " (run seed.india_demo_backfill to re-date the demonstration lot)"))


@group
async def g_tax(P: Proof):
    from app.aurea_core import india_deadlines, tax_india_full, tax_intelligence

    async with P.section("India tax: three taxpayers", "adviser"):
        r = await tax_intelligence.for_household(P.session, P.houses["The Sharma Family"].id, P.firm.jurisdiction, firm_id=P.firm.id)
        tp = {t["name"]: t for t in r["taxpayers"]}
        P.ok("Three taxpayers, each alone and never netted across the family", "adviser", sorted((t["name"], t["kind"]) for t in r["taxpayers"]) ==
             [("Meera Sharma", "individual"), ("Rajesh Sharma", "individual"), ("Rajesh Sharma HUF", "huf")], sorted(tp))
        raj, mee = tp["Rajesh Sharma"], tp["Meera Sharma"]
        P.ok("Rajesh's brought-forward loss is Rs 40,000 long-term and he has a loss to harvest", "adviser",
             raj["brought_forward"]["long_term"] == 40000 and any(o["kind"] == "loss" for o in raj["opportunities"]), raj["brought_forward"])
        P.ok("Meera can use her exemption", "adviser", any(o["kind"] == "exemption" for o in mee["opportunities"]), [o["kind"] for o in mee["opportunities"]])
        await _huf_lot(P, tp["Rajesh Sharma HUF"])
        P.ok("Meera's regime comparison is a finding, and it is an estimate", "adviser",
             mee["engine"]["regime"]["conclusive"] and mee["engine"]["regime"]["cheaper"] == "new" and "qualified person" in raj["engine"]["estimate"], mee["engine"]["regime"]["cheaper"])
        P.ok("The loss ledger has a carried, an expired and a late-return loss", "adviser", sorted(x["state"] for x in raj["engine"]["loss_ledger"]) == ["carried", "expired", "late_return"], sorted(x["state"] for x in raj["engine"]["loss_ledger"]))
        P.ok("Nothing from another country's tax is in it", "adviser", not [w for w in ("KiwiSaver", "bright-line", "Roth", "401(k)", "IRD ", "NZD") if w in str(r)], "clean")
    async with P.section("The tax engine's records", "adviser"):
        r = await P.tool("adviser", "read_india_tax_records")
        n = sum(len(t["losses"]) for t in r["taxpayers"])
        P.ok("Recorded losses and the 31 January 2018 values are read", "adviser", r["applies"] and n >= 3 and "grandfather_values" in r, f"{n} losses")
    async with P.section("Tax habits", "adviser"):
        r = await P.tool("adviser", "read_tax_habits", P.inputs_for("read_tax_habits"))
        P.ok("A household's tax habits are laid out and advise no sale", "adviser", r.get("applies") is not False and "advises a sale" not in str(r.get("message", "")).replace("Nothing here advises a sale", ""), trim(r.get("message")))
    async with P.section("India's dates", "adviser"):
        a = await india_deadlines.household_analysis(P.session, P.firm, P.houses["The Sharma Family"].id)
        f = await india_deadlines.firm_analysis(P.session, P.firm)
        P.ok("The firm's and the household's dates are read", "adviser", bool(a) and bool(f), f"household keys {list(a)[:4]}; firm keys {list(f)[:4]}")
    async with P.section("India's tax figures", "adviser"):
        classes = tax_india_full.asset_classes(P.today)
        P.ok("The asset-class rate table is the law's, each row with a source", "adviser", bool(classes) and all(c.get("source") for c in classes), f"{len(classes)} classes")


@group
async def g_kyc(P: Proof):
    from app.aurea_core import kyc_india

    async with P.section("KYC in every state", "operations"):
        r = await P.tool("operations", "read_kyc")
        st = {p["name"]: p["state"] for p in r["parties"]}
        P.ok("A lapsed KYC reads overdue", "operations", st.get("Lakshmi Iyer") == "overdue", st.get("Lakshmi Iyer"))
        P.ok("A KYC blocked at the KRA reads blocked", "operations", st.get("Nandini Kapoor") == "blocked", st.get("Nandini Kapoor"))
        P.ok("A trust is not assessed and a party with nothing recorded is not on file", "operations", (st.get("Kapoor Family Trust"), st.get("Elaine D'Souza")) == ("not_assessed", "not_on_file"), (st.get("Kapoor Family Trust"), st.get("Elaine D'Souza")))
        P.ok("A verified party is verified", "operations", st.get("Rajesh Sharma") == "verified", st.get("Rajesh Sharma"))
        P.ok("The page says KRA and CKYC lookups are not connected, and that KYC is not compliance", "operations", "not connected" in r["not_connected"] and "does not make the firm compliant" in r["not_compliance"], r["not_connected"][:70])
    async with P.section("An overdue KYC holds the household's book", "operations"):
        iyer = await kyc_india.household_refusal(P.session, P.firm, P.houses["The Iyer Family"].id)
        kap = await kyc_india.household_refusal(P.session, P.firm, P.houses["The Kapoor Family Trust"].id)
        P.ok("The Iyers' orders are held, naming Lakshmi", "operations", bool(iyer) and "Lakshmi Iyer" in iyer, iyer)
        P.ok("The Kapoors' orders are held, naming Nandini", "operations", bool(kap) and "Nandini Kapoor" in kap, kap)
        P.ok("A household with its KYC in order is not held", "operations", not await kyc_india.household_refusal(P.session, P.firm, P.houses["The Sharma Family"].id), "Sharma: none")


@group
async def g_family_estate(P: Proof):
    from app.aurea_core import india_estate, sebi_family

    async with P.section("SEBI's family of client", "adviser"):
        f = await P.tool("adviser", "read_sebi_family", {"household_id": str(P.houses["The Sharma Family"].id)})
        v = await sebi_family.household_view(P.session, P.firm, P.houses["The Sharma Family"].id)
        P.ok("The Sharmas' family is recorded: the household no longer stands in", "adviser", (v["basis"], len(v["families"])) == ("recorded", 2), (v["basis"], len(v["families"])))
        P.ok("The Iyers' household still stands in, and says so", "adviser", (await sebi_family.household_view(P.session, P.firm, P.houses["The Iyer Family"].id))["stands_in"] is not None, "stands_in")
    async with P.section("Estate: HUF, gratuity and nominations", "adviser"):
        e = await india_estate.household_view(P.session, P.firm, P.houses["The Sharma Family"].id)
        P.ok("Rajesh's HUF has its karta and a gratuity of Rs 38,00,000 is on file", "adviser", [h["karta"] for h in e["hufs"]] == ["Rajesh Sharma"] and [g["amount"] for g in e["gratuity"]] == [3_800_000.0], ([h["karta"] for h in e["hufs"]], [g["amount"] for g in e["gratuity"]]))
        b = await india_estate.household_view(P.session, P.firm, P.houses["The Banerjee Household"].id)
        P.ok("A nominee the will does not name is flagged for the legal heirs, never changed", "adviser", any("legal heirs" in i["issue"] for i in b["nomination_issues"]), [i["name"] for i in b["nomination_issues"]])
        t = await P.tool("adviser", "read_india_estate", {})
        P.ok("The estate tool reads the firm", "adviser", t["applies"], f"{len(t['households'])} households")


@group
async def g_governance(P: Proof):
    from app.aurea_core import adviser_certification, counsel_review, governance

    async with P.section("The policy register", "compliance"):
        r = await P.tool("compliance", "read_governance")
        c = (await governance.policies(P.session, P.firm))["counts"]
        P.ok("The register has an overdue policy, one due soon, two current and one not recorded", "compliance", {k: v for k, v in c.items() if v} == {"overdue": 1, "due_soon": 1, "current": 2, "not_recorded": 1} or c["overdue"] >= 1, {k: v for k, v in c.items() if v})
        P.ok("The licence map reads for both licences", "compliance", {l["key"] for l in r["map"]["licences"] if l["held"]} == {"investment_adviser", "pms_discretionary"}, "both held")
    async with P.section("Counsel's review of the law figures", "compliance"):
        c = (await counsel_review.overview(P.session, P.firm))["counts"]
        P.ok("Counsel has reviewed two figures, not confirmed one, and one waits for the Budget", "compliance", (c["confirmed"], c["not_confirmed"], c["awaiting_budget"]) == (2, 1, 1), c)
        P.ok("Every other figure reads not reviewed, which is not the same as wrong", "compliance", c["not_reviewed"] > 50, c["not_reviewed"])
    async with P.section("Adviser certification", "adviser"):
        r = await P.tool("adviser", "read_adviser_certifications")
        p = r["people"][0]
        certs = {x["label"]: x["state"] for x in p["certificates"]}
        P.ok("The adviser's X-A is valid and X-B falls due within 90 days (or has since run out)", "adviser", certs.get("NISM Series X-A") == "valid" and certs.get("NISM Series X-B") in ("due_soon", "expired"), certs)


@group
async def g_reminders(P: Proof):
    from app.models.engagement import Task

    for role in ("compliance", "paraplanner"):
        async with P.section("India reminders: what would be raised (the job is never run)", role):
            r = await P.tool(role, "read_india_reminders")
            P.ok("Dates overdue or near are listed", role, r["applies"] and len(r["reminders"]) >= 6, f"{len(r['reminders'])} dates, {r['to_raise']} without a task")
            P.info("India reminders would raise", role, f"{r['to_raise']} task(s) not yet raised")
    async with P.section("The reminder tasks the seed raised", "compliance"):
        titles = (await P.session.execute(select(Task.title).where(Task.firm_id == P.firm.id, Task.source == "india_reminder"))).scalars().all()
        P.ok("Each is raised once", "compliance", len(titles) >= 6 and len(titles) == len(set(titles)), f"{len(titles)} tasks")
        P.ok("A lapsed KYC, a blocked KYC and a minor now an adult are among them", "compliance",
             any("KYC overdue for Lakshmi Iyer" in t for t in titles) and any("KYC blocked for Nandini Kapoor" in t for t in titles) and any("Tara Menon is now an adult" in t for t in titles), "Lakshmi, Nandini, Tara")


# -- L100 items, batches A, B and C ------------------------------------------------------------------------------------

@group
async def g_gates_accredited_fees(P: Proof):
    from app.aurea_core import accredited, adviser_fees, product_gates

    async with P.section("Ticket-size gates", "adviser"):
        g = await product_gates.for_firm(P.session, P.firm)
        h = {x["household"]: x for x in g["households"]}
        sif = lambda n: next(c for c in h[n]["categories"] if c["category"] == "sif")
        P.ok("A household with an accredited member has the SIF minimum waived", "adviser", sif("The Kapoor Family Trust")["waived"] is True, sif("The Kapoor Family Trust"))
        P.ok("A lapsed certificate does not waive it", "adviser", sif("The Iyer Family")["waived"] is False, sif("The Iyer Family"))
        r = await P.tool("adviser", "read_ticket_gates", P.inputs_for("read_ticket_gates"))
        P.ok("The tool reads one household's gates", "adviser", bool(r), trim(list(r)))
    async with P.section("Accredited investors", "adviser"):
        r = await P.tool("adviser", "read_accredited_investors")
        st = {p["person"]: p for p in r["people"]}
        P.ok("A certificate in force reads accredited", "adviser", st["Nandini Kapoor"]["accredited"] is True and st["Nandini Kapoor"]["state"] == "accredited", st["Nandini Kapoor"]["state"])
        P.ok("A lapsed certificate reads not accredited", "adviser", st["Karthik Iyer"]["accredited"] is False and st["Karthik Iyer"]["state"] == "lapsed", st["Karthik Iyer"]["label"])
        who = await accredited.accredited_persons(P.session, P.firm, P.today)
        P.ok("Only the person in force is counted accredited", "adviser", P.people["Nandini Kapoor"].id in who and P.people["Karthik Iyer"].id not in who, f"{len(who)} accredited")
    async with P.section("An adviser's fee terms since 2025", "adviser"):
        r = await P.tool("adviser", "read_adviser_fee_terms")
        hh = {h["household"]: h for h in r["households"]}
        sh = hh["The Sharma Family"]
        P.ok("The Sharmas pay six months ahead and moved to the AUA mode, the switch kept on record", "adviser", (sh["advance"]["advance_months"], sh["mode"]["mode"]) == (6, "aua") and len(sh["switches"]) >= 1, (sh["advance"]["advance_months"], sh["mode"]["mode"]))
        P.ok("The Menons' second-opinion fee of Rs 40,000 is counted", "adviser", hh["The Menon NRI Household"]["second_opinion"][0]["fee"] == 40000.0, hh["The Menon NRI Household"]["second_opinion"][0]["fee"])
        d = adviser_fees.deposit(P.firm, P.today)
        P.ok("The IAASB deposit is recorded and held in a liquid fund", "adviser", d["recorded"] and d["in_liquid_or_overnight"], d["held_in"])
    async with P.section("The February rate card", "compliance"):
        r = await P.tool("compliance", "read_rate_card")
        P.ok("The long-term equity gains rate is marked awaiting the Budget", "compliance", r["counts"]["awaiting_budget"] == 1 and r["counts"]["not_reviewed"] > 0, r["counts"])


@group
async def g_batch_a(P: Proof):
    async with P.section("Overseas remittances (LRS headroom)", "adviser"):
        r = await P.tool("adviser", "read_lrs")
        k = pick(r["people"], person="Karthik Iyer")
        P.ok("Karthik has remitted $100,000 of $250,000: headroom $150,000, TCS collected", "adviser", bool(k) and (k["limit_usd"], k["remitted_usd"], k["headroom_usd"]) == (250000, 100000.0, 150000.0) and k["tcs_collected"] > 0, k and (k["remitted_usd"], k["headroom_usd"]))
        P.ok("A person's year is assessed from a remittance on file, never estimated", "adviser", {p["state"] for p in r["people"]} <= {"assessed", "not_assessed"} and not any(p["state"] == "assessed" and p["count"] == 0 for p in r["people"]), sorted({p["state"] for p in r["people"]}))
    async with P.section("SIP debit mandates", "adviser"):
        r = await P.tool("adviser", "read_sip_mandates")
        m = pick(r["mandates"], reference="UMRN-DEMO-IYER-0001")
        P.ok("The Iyers' NACH mandate is in force with its per-debit limit", "adviser", bool(m) and m["state"] == "active" and m["per_debit_limit"] == 25000.0, m and (m["state"], m["per_debit_limit"]))
        P.ok("SIPs are listed against their mandates", "adviser", len(r["sips"]) >= 2, f"{len(r['sips'])} SIPs")
    async with P.section("The shelf's other classes", "adviser"):
        r = await P.tool("adviser", "read_india_savings")
        kinds = {h["kind"] for h in r["holdings"]}
        P.ok("A government security and a market-linked debenture are held away, in rupees", "adviser", {"g_sec", "mld"} <= kinds and all(h["currency"] == "INR" for h in r["holdings"]), sorted(kinds))
    async with P.section("Pledged units", "adviser"):
        r = await P.tool("adviser", "read_pledges")
        p = pick(r["pledges"], symbol="NIFTYBEES.NS")
        P.ok("Nifty ETF units under lien are not sellable: sellable is what is held less what is under lien", "adviser",
             bool(p) and p["sellable"] == max(p["held"] - p["under_lien"], 0), p and (p["held"], p["under_lien"], p["sellable"]))


@group
async def g_batch_b(P: Proof):
    async with P.section("Languages and WhatsApp", "adviser"):
        r = await P.tool("adviser", "read_client_channels")
        lang = {h["household"]: h["language"] for h in r["households"]}
        P.ok("The Sharmas read Hindi, the Banerjees Bengali, the D'Souzas have none on file", "adviser", (lang.get("The Sharma Family"), lang.get("The Banerjee Household"), lang.get("The D'Souza Household")) == ("hi", "bn", None), lang)
        opted = [h["household"] for h in r["households"] if h["whatsapp"]["opted_in"]]
        P.ok("Three households opted in to WhatsApp; it is opt-in, never assumed", "adviser", len(opted) == 3, opted)
        tmpl = sorted(t["language"] for t in r.get("templates", []) if t["state"] == "approved")
        P.ok("The annual review template is approved in English and Hindi", "adviser", tmpl == ["en", "hi"], tmpl)
        P.info("WhatsApp provider", "adviser", r.get("provider"))
    async with P.section("DigiLocker, eSign and KRA seams", "adviser"):
        r = await P.tool("adviser", "read_india_stack")
        P.ok("The KRA and CKYC lookup says it is not connected", "adviser", r["applies"] and r["kra_ckyc"]["connected"] is False, r["kra_ckyc"]["said"][:80])
        P.ok("The DigiLocker and eSign state is stated, never implied", "adviser", bool(r["said"]) and "counts" in r, r["said"][:90])


@group
async def g_batch_c(P: Proof):
    async with P.section("Mutual fund orders: rail and NAV day", "operations"):
        r = await P.tool("operations", "read_mf_orders")
        P.ok("Four fund orders carry a rail, one NAV day differs from the rule and one is not assessed", "operations", (len(r["orders"]), r["breaks"], r["not_assessed"]) == (4, 1, 1), (len(r["orders"]), r["breaks"], r["not_assessed"]))
        d = pick(r["orders"], state="differs")
        P.ok("A differing NAV day is reported with a likely cause, never repaired", "operations", bool(d) and d["gap_days"] == 3 and bool(d["likely_cause"]), d and d["gap_days"])
    async with P.section("Advice kept apart from distribution", "compliance"):
        r = await P.tool("compliance", "read_distribution_separation")
        P.ok("Three group entities and one flag for compliance", "compliance", (len(r["entities"]), len(r["check"]["flags"])) == (3, 1), (len(r["entities"]), len(r["check"]["flags"])))
        seen = sorted(e["determination"] for e in r["entities"] if e["holds_arn"])
        P.ok("The flagged entity awaits counsel; the associate counsel determined outside the family is not flagged", "compliance",
             [f["determination"] for f in r["check"]["flags"]] == ["awaiting_counsel"] and seen == ["awaiting_counsel", "outside"], seen)
        cmp_ = r["comparison"]
        P.ok("Beside a comparison of direct and regular expense ratios; a scheme with no ratio on file is not assessed, never guessed", "compliance",
             "holdings" in cmp_ and "not_assessed" in cmp_, f"{len(cmp_['holdings'])} holding(s), {cmp_['not_assessed']} not assessed")
    async with P.section("Advertisement pre-approval", "compliance"):
        r = await P.tool("compliance", "read_ad_approvals")
        P.ok("Three marketing pieces: one approved, one rejected, one waiting", "compliance", (r["approved"], r["rejected"], r["pending"]) == (1, 1, 1), (r["approved"], r["rejected"], r["pending"]))


@group
async def g_batch_e(P: Proof):
    from app.aurea_core import client_channel_request, pre_advice

    async with P.section("What each advisory agreement carried", "adviser"):
        gated = await pre_advice.for_firm(P.session, P.firm)
        got = {g["household"]: next(c["state"] for c in g["checks"] if c["key"] == "agreement") for g in gated}
        P.ok("Every gated household's agreement passes: Annexure A and the MITC are recorded (needs seed.india_demo_backfill)", "adviser",
             bool(got) and set(got.values()) == {"pass"}, got)
        r = await P.tool("adviser", "read_agreement_contents", P.inputs_for("read_agreement_contents"))
        P.ok("The Sharmas' agreement is recorded as carrying both", "adviser", r["annexure_a"] is True and r["mitc"] is True, (r["annexure_a"], r["mitc"], r["source"]))
    async with P.section("Mutual fund cut-off figures", "operations"):
        r = await P.tool("operations", "read_mf_figures")
        P.ok("A figure the firm recorded reads recorded and the others read assumed", "operations",
             len(r["figures"]) == 4 and r["recorded"] >= 1 and all(f["state_label"] == "assumed" or f["state_label"].startswith("recorded: ") for f in r["figures"]),
             (r["recorded"], r["assumed"]))
    async with P.section("A client's own language and WhatsApp requests (the page's read)", "client"):
        v = await client_channel_request.view(P.session, P.firm, P.houses["The Sharma Family"].id)
        P.ok("The client's page reads what is recorded for them, and offers the languages to ask for", "client",
             v["applies"] and v["language"] == "hi" and len(v["languages"]) == 8, (v["language"], v["whatsapp_on_file"]))


# -- the conversation as every role, and the client ------------------------------------------------------------------------

@group
async def g_tool_sweep(P: Proof):
    """Every India read tool, run as every role that holds it, through the gateway's gate and the app's executors."""
    from app.core.tools import tools_for_role
    from app.models.enums import UserRole

    for key in INDIA_READ_TOOLS:
        holders = [r for r in STAFF_ROLES if key in tools_for_role(UserRole(r)) and r in P.users]
        ran, bad = [], []
        for role in holders:
            try:
                res = await P.tool(role, key, P.inputs_for(key))
                (ran if isinstance(res, dict) and res.get("applies", True) is not False else bad).append(role)
            except WriteBlocked as exc:
                bad.append(f"{role}: WROTE ({exc})")
                await P.reset()
            except Exception as exc:      # noqa: BLE001
                bad.append(f"{role}: {type(exc).__name__} {trim(exc, 70)}")
                await P.reset()
        P.ok(f"{key} runs for every role that holds it", "+".join(holders) or "none", bool(holders) and not bad, f"ran for {len(ran)}/{len(holders)}" + (f"; {bad}" if bad else ""))


@group
async def g_client(P: Proof):
    from app.aurea_core import consents as consents_core
    from app.aurea_core import disclosures

    async with P.section("A client reads their own household", "client"):
        r = await P.tool("client", "read_my_wealth")
        P.ok("The client reads the Sharma household, in rupees", "client", r["household_id"] == str(P.houses["The Sharma Family"].id) and r["currency"] == "INR", (r["household_id"][:8], r["currency"]))
        P.ok("Goals carry their likelihood as an estimate", "client", bool(r["goals"]) and all(g["estimate"] for g in r["goals"]), f"{len(r['goals'])} goals")
    P.ok("India's consent kinds and disclosures are India's", "-", "risk_profile" in consents_core.kinds_for(P.firm) and "in_investor_charter" in disclosures.required_for("IN", "individual"), "risk_profile; in_investor_charter")


# --------------------------------------------------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------------------------------------------------

async def load(P: Proof) -> bool:
    from app.aurea_core import firm_time
    from app.models.graph import Household, Person
    from app.models.identity import User
    from app.models.tenant import Firm

    s = P.session
    P.firm = (await s.execute(select(Firm).where(Firm.slug == SLUG))).scalar_one_or_none()
    if P.firm is None:
        P.ok("The Indian demonstration firm exists", "-", False, f"no firm with slug {SLUG}")
        return False
    P.today = firm_time.today(P.firm)
    for u in (await s.execute(select(User).where(User.firm_id == P.firm.id).order_by(User.email))).scalars().all():
        P.users.setdefault(u.role.value if hasattr(u.role, "value") else str(u.role), u)
    P.houses = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == P.firm.id))).scalars().all()}
    P.people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == P.firm.id))).scalars().all()}
    P.ok("The Indian demonstration firm exists", "-", True, f"{P.firm.name}, {len(P.houses)} households, {len(P.people)} people, {len(P.users)} sign-in roles, today {P.today}")
    return True


async def run(session, out=print, *, selftest: bool = True) -> Proof:
    """Run every group against `session`, which must not be one anything else is using. Nothing is committed."""
    P = Proof(session, out)
    if selftest:
        for ok, what in await guard_selftest():
            P.ok("The write guards", "self-test (in-memory)", ok, what)
    remove = install_guards(session)
    try:
        await make_read_only(session)
        if await load(P):
            for g in GROUPS:
                try:
                    await g(P)
                except Exception as exc:      # noqa: BLE001 - a group that fails to start is itself a FAIL
                    P.ok(g.__name__, "-", False, f"group raised {type(exc).__name__}: {trim(exc, 120)}")
                    await P.reset()
    finally:
        try:
            await session.rollback()
        finally:
            remove()
    P.out(f"\n{P.passed} PASS, {P.failed} FAIL, {len(P.infos)} INFO. Nothing was written: the session was only rolled back.")
    return P


async def main() -> int:
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        P = await run(session)
    return 1 if P.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
