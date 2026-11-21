"""A persona from public sources (B-202, aurea_core/persona.py and news_sources.py).

    python -m tests.verify_persona

Proves: profiling is off for every firm until compliance records a lawful basis and switches it on, with a reason on
the audit log, and a refusal names compliance; nobody else sets it; each talking point cites its article and quotes
words that are in it, is dated and expires after 180 days; nothing about a special category, no adverse media and no
expired article becomes a point, and each is counted; a point a model offers that is not in the article, or touches a
special category, is dropped; a simulated source says so; the prospect brief carries the persona only where it runs;
a client holds no persona tool; another firm's people are not found. Runs against a throwaway SQLite database, with a
news source registered by the test itself (no simulation needed).
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.aurea_core import brief_profile, firm_time, news_sources, persona
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.identity import AuditEvent, User
from tests import _sim_fixture

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, persona.PersonaError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:140]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


class TestSource:
    key, name = "test_wire", "Test wire"

    def __init__(self, firm_id, simulated=False):
        self.firm_id, self.simulated = firm_id, simulated

    def applies_to(self, firm) -> bool:
        return firm.id == self.firm_id

    async def articles(self, firm, *, name: str, company=None):
        today = firm_time.today(firm)
        if persona.plain(name) != "james whitaker":
            return []
        A = news_sources.Article
        return [
            A("Whitaker Logistics opens a hub", "Test Business Daily", today - timedelta(days=10), "https://t.example/hub",
              "James Whitaker said the hub would double the firm's export capacity. He chairs the chamber's export committee. "
              "Whitaker, who is recovering from surgery, attended. He voted for the Green Party last year. "
              "He attends St Mary's church every week. His union membership was renewed."),
            A("Yacht takes line honours", "Test Sailing News", today - timedelta(days=40), "https://t.example/yacht",
              "Skippered by James Whitaker, the yacht took line honours in the harbour regatta. "
              "Police said he was arrested in 2001."),
            A("Supplier files lawsuit", "Test Business Daily", today - timedelta(days=20), "https://t.example/suit",
              "A lawsuit names Whitaker Logistics. He loves sailing and golf."),
            A("Old award story", "Test Business Daily", today - timedelta(days=400), "https://t.example/old",
              "James Whitaker accepted the exporter award."),
            A("Tomorrow's story", "Test Business Daily", today + timedelta(days=3), "https://t.example/next",
              "James Whitaker will speak at the conference."),
        ]


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            fx = await _sim_fixture.build(s)
            firm, sophie, admin = fx.firm, fx.adviser, fx.admin
            comp = User(id=uuid.uuid4(), firm_id=firm.id, email="c@demo", full_name="Cy Compliance", role=UserRole.COMPLIANCE,
                        hashed_password="x", is_active=True)
            s.add(comp)
            await s.flush()
            james = fx.contacts["James Whitaker"]
            ex = lambda u, f=firm: ToolExecutors(s, u.id, u.role, f.id)  # noqa: E731
            src = TestSource(firm.id)
            news_sources.register(src)

            print("\n=== Off until compliance records a lawful basis ===")
            check("off by default", persona.control(firm)["on"], False)
            await refused("reading is refused, naming compliance", ex(sophie).execute("read_persona", {"contact_id": str(james.id)}),
                          "off at this firm", "compliance")
            brief = await ex(sophie).execute("prepare_prospect_meeting", {"contact_id": str(james.id)})
            check("the brief carries no persona", ("persona" in brief, "persona" in brief["shaped_for"]["order"]), (False, False))
            await refused("switching on with no basis", ex(comp).preview_change("set_agent_control",
                          {"key": "persona_profiling", "value": "true", "reason": "Demo"}), "lawful basis")
            await refused("an administrator may not set the basis", ex(admin).preview_change("set_agent_control",
                          {"key": "persona_basis", "value": "Legitimate interests", "reason": "r"}), "compliance's to set")
            await refused("nor an adviser", ex(sophie).preview_change("set_agent_control",
                          {"key": "persona_profiling", "value": "true", "reason": "r"}), "compliance's to set")
            basis = "Legitimate interests, privacy notice section 4.2: public professional information to prepare meetings"
            card = {r["label"]: r["value"] for r in (await ex(comp).preview_change("set_agent_control",
                    {"key": "persona_basis", "value": basis, "reason": "Privacy notice updated 1 Nov"}))["rows"]}
            check("the card names the basis, now and after", (card["Now"], card["Becomes"]), ("Not set", basis))
            await ex(comp).execute("set_agent_control", {"key": "persona_basis", "value": basis, "reason": "Privacy notice updated 1 Nov"})
            check("a basis alone does not switch it on", persona.control(firm)["on"], False)
            await ex(comp).execute("set_agent_control", {"key": "persona_profiling", "value": "true", "reason": "Approved by the privacy officer"})
            check("on, with its basis", (persona.control(firm)["on"], persona.control(firm)["basis"]), (True, basis))
            audit = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "foundation.setting_set"))).scalars().all()
            check("both on the audit log with their reasons", sorted((a.detail["key"], a.detail["reason"]) for a in audit),
                  [("persona_basis", "Privacy notice updated 1 Nov"), ("persona_profiling", "Approved by the privacy officer")])
            await refused("the basis is not cleared while it runs", ex(comp).preview_change("set_agent_control",
                          {"key": "persona_basis", "value": "", "reason": "r"}), "switch persona_profiling off first")
            book = await ex(comp).execute("read_compliance_rules", {})
            owners = {x["key"]: (x["owner"], x["kind"]) for x in book["settings"]}
            check("the rulebook lists both as compliance's", (owners["persona_profiling"], owners["persona_basis"]),
                  (("compliance", "bool"), ("compliance", "text")))

            print("\n=== Talking points ===")
            out = await ex(sophie).execute("read_persona", {"contact_id": str(james.id)})
            labels = sorted(p["point"] for p in out["points"])
            check("interests at work and beyond, from the lexicon with no model", (labels, out["extraction"]),
                  (["Board and governance roles", "Growing the business", "Sailing"], "rules"))
            today = firm_time.today(firm)
            hub = next(p for p in out["points"] if p["point"] == "Growing the business")
            check("each cites its article: title, publisher, date, link",
                  (hub["article"]["title"], hub["article"]["publisher"], hub["dated"], hub["article"]["url"]),
                  ("Whitaker Logistics opens a hub", "Test Business Daily", (today - timedelta(days=10)).isoformat(), "https://t.example/hub"))
            check("and quotes words that are in it", hub["quote"], "James Whitaker said the hub would double the firm's export capacity.")
            check("it expires 180 days after the article", hub["expires_on"], (today - timedelta(days=10) + timedelta(days=180)).isoformat())
            words = " ".join(f"{p['point']} {p['quote']}" for p in out["points"]).lower()
            check("nothing on health, politics, religion, unions or crime reaches a point",
                  [w for w in ("surgery", "green party", "church", "union", "arrested", "police") if w in words], [])
            left = out["left_out"]
            check("left out as counts, by category", (left["special_category"], sorted(left["by_category"])),
                  (5, ["criminal", "health", "politics", "religion", "union"]))
            check("an adverse article is left whole, for screening", (left["adverse_media"], "golf" in words), (1, False))
            check("an article past the expiry is counted, a future one ignored", (left["expired"], out["articles_read"]), (1, 5))
            check("a real source is not marked simulated", (out["simulated"], out["sources"][0]["simulated"]), (False, False))
            check("the basis is carried with it", out["control"]["basis"], basis)

            print("\n=== A model's points are checked against the article ===")
            text = "James Whitaker said the hub would double the firm's export capacity. He chairs the chamber's export committee."
            check("a quote in the article stands", persona.checked({"point": "Exports", "domain": "work",
                  "quote": "He chairs the chamber's export committee."}, text), True)
            check("a quote not in the article falls", persona.checked({"point": "Exports", "domain": "work",
                  "quote": "He is opening a hub in Sydney next year."}, text), False)
            check("a point on a special category falls", persona.checked({"point": "Recovering from surgery", "domain": "beyond",
                  "quote": "He chairs the chamber's export committee."}, text), False)
            check("an unknown domain falls", persona.checked({"point": "Exports", "domain": "family",
                  "quote": "He chairs the chamber's export committee."}, text), False)
            real = persona._model_points

            async def fake(session, firm_, who, texts):
                a, t = texts[0]
                return [{"point": "Export growth", "domain": "work", "quote": "would double the firm's export capacity", "_article": a},
                        {"point": "Invented", "domain": "work", "quote": "He has bought a football club in Leeds.", "_article": a},
                        {"point": "Faith", "domain": "beyond", "quote": "would double the firm's export capacity", "_article": a}]
            persona._model_points = fake
            try:
                out = await ex(sophie).execute("read_persona", {"contact_id": str(james.id)})
            finally:
                persona._model_points = real
            check("a model's checked point stands, the others are dropped and counted",
                  ([p["point"] for p in out["points"]], out["left_out"]["unchecked"], out["extraction"]), (["Export growth"], 2, "model"))

            print("\n=== Simulated, elsewhere, and never a client's ===")
            src.simulated = True
            out = await ex(sophie).execute("read_persona", {"name": "james whitaker"})
            check("a simulated source says so on the persona and each point",
                  (out["simulated"], all(p["simulated"] for p in out["points"])), (True, True))
            src.simulated = False
            brief = await ex(sophie).execute("prepare_prospect_meeting", {"contact_id": str(james.id)})
            check("the brief carries the persona where it runs", ("persona" in brief, "persona" in brief["shaped_for"]["order"],
                  len(brief["persona"]["points"])), (True, True, 3))
            check("the persona is a prospect brief section", ("persona", "Persona") in brief_profile.SECTIONS["prospect"], True)
            grace = await ex(sophie).execute("read_persona", {"contact_id": str(fx.contacts["Grace Liu"].id)})
            check("nobody written about: no point, nothing invented", (grace["points"], grace["articles_read"]), ([], 0))
            check("no persona tool for a client", any(k in tools_for_role(UserRole.CLIENT) for k in ("read_persona", "read_network_map",
                  "import_linkedin_connections")), False)
            check("reading a persona changes nothing", TOOLS["read_persona"].change_state, ToolChangeState.NO)
            await refused("a client is refused outright", ToolExecutors(s, uuid.uuid4(), UserRole.CLIENT, firm.id).execute(
                "read_persona", {"contact_id": str(james.id)}), "never shown to a client")
            await refused("another firm's contact is not found", ex(sophie, fx.other).execute("read_persona", {"contact_id": str(james.id)}),
                          "not one of this firm's")
            check("a source answers only the firm it applies to", [x.key for x in news_sources.for_firm(fx.other)], [])
            news_sources.unregister(src.key)
            out = await ex(sophie).execute("read_persona", {"contact_id": str(james.id)})
            check("no source registered: says so, invents nothing", (out["points"], out["not_on_file"]), ([], ["A licensed news source"]))
            await ex(comp).execute("set_agent_control", {"key": "persona_profiling", "value": "false", "reason": "Notice withdrawn"})
            await refused("switched off, it is refused again", ex(sophie).execute("read_persona", {"contact_id": str(james.id)}), "compliance")
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
