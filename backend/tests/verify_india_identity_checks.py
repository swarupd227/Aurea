"""Prove every identity and bank check has a path for failure, and a failed one never reads as passed (B-387).

PAN verification, the penny drop with its scored name match and video in-person verification each record their outcome; a provider
is asked at most three times; a partial name or used-up retries go to a person; only operations or compliance record a result by
hand, from the document; the activation gate and the board's KYC queue fail while a check run has not passed; with no service
connected nothing is checked; and the only service is the simulation, opt-in, tagged and discardable.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_identity_checks
"""
from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import onboarding as api
from app.aurea_core import identity_checks as c, india_money, money_in, onboarding_start
from app.core.db import Base
from app.core.tools import CLIENT_TOOLS, TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.identity_checks import VerificationAttempt
from app.models.money_in import BankLink
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
        check(label, "accepted", "refused")
    except (c.CheckError, ExecutorError) as exc:
        said = str(exc)
        check(label, all(x.lower() in said.lower() for x in contains) or said, True)


class Scripted:
    """A stand-in service whose next answers the test names."""
    key = "simulated:scripted"
    name = "Scripted service (test)"
    simulated = True

    def __init__(self):
        self.answers: list[dict] = []
        self.asked = 0

    def enabled_for(self, firm) -> bool:
        return True

    async def check(self, session, firm, **kw) -> dict:
        self.asked += 1
        return self.answers.pop(0)


def names() -> None:
    print("\n=== The name match is scored, and initials never pass ===")

    def s(shown, name="Rajesh Sharma"):
        return c.name_score(shown, name)["score"]

    def band(score):
        return "pass" if score >= c.PASS_SCORE else "review" if score >= c.REVIEW_SCORE else "fail"

    check("the same name, honorific and order aside", (s("RAJESH SHARMA"), s("Shri Rajesh Sharma"), s("Sharma Rajesh")), (100, 100, 100))
    check("a middle name added passes", band(s("Rajesh Kumar Sharma")), "pass")
    check("R Sharma goes to a person, never passes", band(s("R Sharma")), "review")
    check("a bare surname fails", band(s("Sharma")), "fail")
    check("another given name with the same surname fails", band(s("Meera Sharma")), "fail")
    check("a different name fails", band(s("Anil Gupta")), "fail")
    check("a transliteration goes to a person", band(s("Laxmi Iyer", "Lakshmi Iyer")), "review")
    check("nothing to compare is zero", s(""), 0)
    check("an answer with no name is a failure", c.evaluate("bank", {"result": "ok"}, "Rajesh Sharma")["outcome"], "failed")
    check("no answer is unavailable, not passed", c.evaluate("vipv", {"result": "unavailable"}, "x")["outcome"], "unavailable")
    check("an unknown result is not a pass", c.evaluate("vipv", {"result": "pending"}, "x")["outcome"], "failed")


async def main() -> int:
    names()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            await run(s)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def run(s) -> None:
    firm = Firm(id=uuid.uuid4(), name="Astra India", slug="india-demo", jurisdiction="IN", regulator="SEBI", base_currency="INR",
                settings={"timezone": "Asia/Kolkata"})
    other = Firm(id=uuid.uuid4(), name="Astra India Two", slug="two", jurisdiction="IN", regulator="SEBI", base_currency="INR", settings={})
    us = Firm(id=uuid.uuid4(), name="Astra US", slug="us", jurisdiction="US", regulator="SEC", base_currency="USD", settings={})
    s.add_all([firm, other, us])
    await s.flush()
    users = {}
    for key, role, f in (("adv", UserRole.ADVISER, firm), ("ops", UserRole.OPERATIONS, firm), ("comp", UserRole.COMPLIANCE, firm),
                         ("pm", UserRole.PORTFOLIO_TEAM, firm), ("two", UserRole.OPERATIONS, other), ("us", UserRole.OPERATIONS, us)):
        u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{key}@t", full_name=key.title(), role=role, hashed_password="x", is_active=True)
        s.add(u)
        users[key] = u
    home = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Sharma Family", values={})
    elsewhere = Household(id=uuid.uuid4(), firm_id=firm.id, name="Another Family", values={})
    s.add_all([home, elsewhere])
    await s.flush()
    rajesh = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=home.id, full_name="Rajesh Sharma", profile={}, pan="ABCPS1234F")
    meera = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=elsewhere.id, full_name="Meera Gupta", profile={})
    s.add_all([rajesh, meera])
    await s.flush()
    mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=rajesh.id, name="Rajesh - Advisory", mandate_type="advisory")
    s.add(mandate)
    await s.flush()
    account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="Rajesh demat", currency="INR", cash_balance=0)
    s.add(account)
    await s.flush()
    ex = {key: ToolExecutors(s, u.id, u.role, u.firm_id) for key, u in users.items()}
    ops = UserRole.OPERATIONS
    case = await onboarding_start.start(s, firm, actor=users["adv"], prospect_name="Nila Iyer", registration_type="individual")

    async def count() -> int:
        return (await s.execute(select(func.count()).select_from(VerificationAttempt))).scalar_one()

    async def gate():
        return next((g for g in (await api._gates_for(s, firm, case))["gates"] if g["key"] == "identity_checks"), None)

    c.PROVIDERS.clear()
    print("\n=== With no service connected nothing is checked ===")
    on_case = {"case_id": str(case.id)}
    await refused("a run says no service is connected", ex["ops"].execute("run_identity_check", {"kind": "vipv", **on_case}), "no pan, penny-drop or video")
    check("nothing recorded", await count(), 0)
    check("a case with no check run has no gate, and never a pass", await gate(), None)
    read = await ex["ops"].execute("read_identity_checks", {})
    check("the read says what happens on failure, for each", [(d["kind"], d["tries"], bool(d["alternative"])) for d in read["defined"]],
          [("pan", 3, True), ("bank", 3, True), ("vipv", 3, True)])
    check("and that a check not listed was not run", "not run, which is not a pass" in read["message"], True)

    print("\n=== Retry, then a person ===")
    service = Scripted()
    c.register(service)
    service.answers = [{"result": "failed", "reason": "liveness_failed"}, {"result": "unavailable"}, {"result": "failed", "reason": "face_mismatch"}]
    args = {"kind": "vipv", **on_case}
    card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("run_identity_check", args))["rows"]}
    check("the card names the attempt, the service and the failure path", (card["Attempt"], card["Simulated"].startswith("Yes"), "in-person verification" in card["If it fails"]),
          ("1 of 3", True, True))
    check("the card asks the service nothing", (service.asked, await count()), (0, 0))
    first = await ex["adv"].execute("run_identity_check", args)
    check("a failure says it can be retried and has not passed", (first["attempted"]["state"], "has not passed" in first["message"], "Liveness" in first["message"] or "liveness" in first["message"]),
          ("retry", True, True))
    g = await gate()
    check("the activation gate fails on it", (g["state"], g["blocks"]), ("fail", True))
    queues = await api.states_overview(s, firm)
    check("and it is a line in the board's KYC queue", any("Video in-person verification for Nila Iyer" in i["detail"] for i in queues["queues"]["kyc"]["items"]), True)
    second = await ex["adv"].execute("run_identity_check", args)
    check("no answer is not a pass", (second["attempted"]["outcome"], second["attempted"]["state"]), ("unavailable", "retry"))
    third = await ex["adv"].execute("run_identity_check", args)
    check("the third failure goes to the queue", (third["attempted"]["state"], third["counts"]["queued"], third["queue"][0]["owner"]),
          ("queued", 1, c.QUEUE_OWNER))
    await refused("the service is not asked a fourth time", ex["adv"].execute("run_identity_check", args), "with a person")
    check("three asks, three attempts", (service.asked, await count()), (3, 3))
    await refused("an adviser does not record the result by hand", ex["adv"].execute("record_verification_result", {**args, "outcome": "passed", "document": "IPV record"}), "operations or compliance")
    await refused("nor without the document", ex["ops"].execute("record_verification_result", {**args, "outcome": "passed"}), "which document")
    done = await ex["ops"].execute("record_verification_result", {**args, "outcome": "passed", "document": "In-person verification signed by the officer"})
    check("by hand it passes, and says by hand", done["attempted"]["state"], "passed_alternative")
    check("the history keeps all four", [h["outcome"] for h in done["checks"][0]["history"]], ["Failed", "No answer", "Failed", "Passed"])
    g = await gate()
    check("the gate passes what was run and names what was not", (g["state"], "Not run: PAN verification for Nila Iyer" in g["detail"]), ("pass", True))

    print("\n=== A partial name goes to a person ===")
    service.answers = [{"result": "ok", "name": "R Sharma"}]
    bank = {"kind": "bank", "person_id": str(rajesh.id), "bank": "A demonstration bank", "last4": "4321"}
    got = await ex["ops"].execute("run_identity_check", bank)
    check("R Sharma is review, not passed", (got["attempted"]["state"], got["attempted"]["score"]), ("review", 65))
    await refused("and is not retried", ex["ops"].execute("run_identity_check", bank), "with a person")
    await refused("a pass by hand needs the name on the document", ex["ops"].execute("record_verification_result", {**bank, "outcome": "passed", "document": "Cancelled cheque"}), "name exactly")
    await refused("a name that does not match cannot pass", ex["ops"].execute("record_verification_result", {**bank, "outcome": "passed", "document": "Cancelled cheque", "name_on_document": "Anil Gupta"}), "cannot be recorded as passed")
    await refused("initials need the reason", ex["ops"].execute("record_verification_result", {**bank, "outcome": "passed", "document": "Cancelled cheque", "name_on_document": "R Sharma"}), "only in part")
    check("no refusal wrote anything", await count(), 5)
    bad = await ex["comp"].execute("record_verification_result", {**bank, "outcome": "failed", "document": "Bank statement shows another holder", "name_on_document": "Ramesh Sharma"})
    check("failed by hand stays failed and queued", (bad["attempted"]["state"], bad["counts"]["queued"]), ("failed", 1))
    fixed = await ex["ops"].execute("record_verification_result", {**bank, "outcome": "passed", "document": "Bank letter with the full name", "name_on_document": "Rajesh Sharma"})
    check("a later document passes it", (fixed["attempted"]["state"], fixed["counts"]["queued"]), ("passed_alternative", 0))
    service.answers = [{"result": "failed", "reason": "account_not_found"}]
    second_account = await ex["ops"].execute("run_identity_check", {**bank, "last4": "9999"})
    check("each account is its own check", sorted((r["last4"], r["state"]) for r in second_account["checks"] if r["kind"] == "bank"),
          [("4321", "passed_alternative"), ("9999", "retry")])

    print("\n=== PAN ===")
    await refused("no PAN on file and none given", ex["ops"].execute("run_identity_check", {"kind": "pan", **on_case}), "no pan to verify")
    service.answers = [{"result": "ok", "name": "Rajesh Sharma"}]
    card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("run_identity_check", {"kind": "pan", "person_id": str(rajesh.id)}))["rows"]}
    check("the PAN on file is used, shown masked", card["PAN"], "••••••234F")
    got = await ex["ops"].execute("run_identity_check", {"kind": "pan", "person_id": str(rajesh.id)})
    row = (await s.execute(select(VerificationAttempt).where(VerificationAttempt.kind == "pan"))).scalars().one()
    check("passed, with only the masked PAN kept", (got["attempted"]["state"], row.pan_masked, "ABCPS1234F" in str(got)), ("passed", "••••••234F", False))
    await refused("a passed check is not run again", ex["ops"].execute("run_identity_check", {"kind": "pan", "person_id": str(rajesh.id)}), "already passed")

    print("\n=== Scope ===")
    link = BankLink(id=uuid.uuid4(), firm_id=firm.id, account_id=account.id, owner_name="Rajesh Sharma", bank="A demonstration bank", last4="5555",
                    method="instant", verified_on=__import__("datetime").date(2026, 1, 1), status="verified", evidence="x", recorded_by="x")
    s.add(link)
    await s.flush()
    await refused("another household's registered bank is not this client's", ex["ops"].execute("run_identity_check", {"kind": "bank", "person_id": str(meera.id), "bank_link_id": str(link.id)}), "not registered")
    service.answers = [{"result": "ok", "name": "Rajesh Sharma"}]
    got = await ex["ops"].execute("run_identity_check", {"kind": "bank", "person_id": str(rajesh.id), "bank_link_id": str(link.id)})
    check("their own is checked by its last four", got["attempted"]["state"], "passed")
    await refused("another firm's client is nobody", ex["two"].execute("run_identity_check", {"kind": "vipv", "person_id": str(rajesh.id)}), "no such client")
    await refused("another firm's case", ex["two"].execute("read_identity_checks", {"case_id": str(case.id)}), "no such onboarding case")
    check("another firm reads none", (await c.overview(s, other))["checks"], [])
    check("outside India there are none", await c.overview(s, us), {"applies": False})
    await refused("a portfolio manager runs none", ex["pm"].execute("run_identity_check", args), "run by")

    print("\n=== A surname alone no longer makes a bank account the household's own (the token-subset hole) ===")
    async def link_for(owner, **kw):
        return await money_in.prepare_link(s, firm.id, account_id=account.id, owner_name=owner, bank="A bank", last4="1111", method="instant",
                                           evidence="Penny drop advice", verified_on=None, **kw)

    check("a full name is first party", (await link_for("Rajesh Sharma"))["third_party"], False)
    for owner, why in (("Sharma", "a bare surname"), ("M Sharma", "an initial that is nobody's here")):
        try:
            got = await link_for(owner)
            check(f"{why} needs a callback", got["third_party"], "refused")
        except money_in.MoneyInError as exc:
            check(f"{why} needs a callback", "callback" in str(exc), True)
    check("with one it is recorded as a third party", (await link_for("Sharma", callback_by="Adv, on the number of record"))["third_party"], True)
    check("initials that fit the member register, as before", (await link_for("R Sharma"))["third_party"], False)
    paid = await india_money.check_payer(s, firm, account, payer_name="R Sharma", last4="1111")
    check("and still pay nothing until the full name is confirmed", (paid["ok"], "initials" in paid["said"]), (False, True))

    print("\n=== The tools ===")
    check("both writes are behind a Confirm card", [TOOLS[t].change_state for t in ("run_identity_check", "record_verification_result")],
          [ToolChangeState.PAUSE_FOR_CONFIRMATION] * 2)
    check("the read changes nothing", TOOLS["read_identity_checks"].change_state, ToolChangeState.NO)
    check("a client holds none", [t for t in ("read_identity_checks", "run_identity_check", "record_verification_result") if t in CLIENT_TOOLS], [])
    check("only operations or compliance record by hand", sorted(r.value for r in TOOLS["record_verification_result"].roles_required),
          ["compliance", "operations"])

    print("\n=== The simulation: opt-in, tagged, discardable ===")
    from app import simulations
    from app.simulations import identity_checks as sim

    c.PROVIDERS.clear()
    sim.install()
    other_case = await onboarding_start.start(s, firm, actor=users["adv"], prospect_name="Devendra Rao", registration_type="individual")
    await refused("installed is not opted in", ex["ops"].execute("run_identity_check", {"kind": "vipv", "case_id": str(other_case.id)}), "no pan, penny-drop or video")
    before = await count()
    await sim.seed(s, firm)
    check("seeding only opts in", (simulations.enabled_for(firm, "identity_checks"), await count()), (True, before))
    one = await ex["ops"].execute("run_identity_check", {"kind": "vipv", "case_id": str(other_case.id)})
    check("a scripted first video attempt fails, simulated", (one["attempted"]["state"], one["attempted"]["simulated"], one["message"].startswith("Simulated service")),
          ("retry", True, True))
    two = await ex["ops"].execute("run_identity_check", {"kind": "vipv", "case_id": str(other_case.id)})
    check("the retry passes", two["attempted"]["state"], "passed")
    initials = await ex["ops"].execute("run_identity_check", {"kind": "bank", "case_id": str(other_case.id), "bank": "A demonstration bank", "last4": "1111"})
    check("a bank that shows initials goes to a person", initials["attempted"]["state"], "review")
    missing = await ex["ops"].execute("run_identity_check", {"kind": "bank", "case_id": str(other_case.id), "bank": "A demonstration bank", "last4": "0000"})
    check("an account that does not exist fails", (missing["attempted"]["state"], missing["attempted"]["reason"]), ("retry", "No account with these details"))
    tagged = (await s.execute(select(VerificationAttempt).where(VerificationAttempt.case_id == other_case.id))).scalars().all()
    check("every attempt it answered is tagged", {a.source_system for a in tagged}, {"simulated:identity_checks"})
    await sim.discard(s, firm)
    check("discarding removes them and the opt-in", (await count(), simulations.enabled_for(firm, "identity_checks")), (before, False))
    c.PROVIDERS.clear()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
