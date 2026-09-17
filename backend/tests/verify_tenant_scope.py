"""Every read of a household is scoped to a firm — checked statically, and behaviourally.

household_brain used to look a household up by id alone, so another firm's client brain
was one known id away: through the household routes, the Canvas staff preview, and running
any agent with that household as its subject. It now takes a required, keyword-only
firm_id.

Keyword-only means a caller that omits it does not fail at import. It raises TypeError
only when that code path runs — possibly inside an agent that is scheduled monthly. So
this walks the whole backend's syntax tree and fails on any call that leaves it out,
rather than waiting for that path to run.

    python -m tests.verify_tenant_scope
"""
from __future__ import annotations

import ast
import asyncio
import sys
import uuid
from pathlib import Path

PASS, FAIL = [], []

# Functions whose household reads must carry a firm.
SCOPED = {"household_brain", "for_household"}


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(f"{label}: got {got}, want {want}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def unscoped_calls(root: Path) -> list[str]:
    missing = []
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if name not in SCOPED:
                continue
            if not any(k.arg == "firm_id" for k in node.keywords):
                missing.append(f"{path.relative_to(root.parent)}:{node.lineno} {name}()")
    return missing


def signatures_require_it(root: Path) -> list[str]:
    """The definitions themselves must make firm_id keyword-only with no default."""
    wrong = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in SCOPED:
                kwonly = {a.arg: d for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults)}
                if "firm_id" not in kwonly or kwonly["firm_id"] is not None:
                    wrong.append(f"{path.relative_to(root.parent)}:{node.lineno} {node.name}")
    return wrong


async def behaviour() -> None:
    """A household from another firm reads as not found."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.aurea_core.graph import household_brain
    from app.core.db import Base
    from app.models.graph import Household
    from app.models.tenant import Firm

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [Base.metadata.tables[n] for n in
              ("firm", "household", "person", "legal_entity", "mandate", "account",
               "goal", "relationship_edge", "holding", "instrument", "price")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as s:
        mine = Firm(id=uuid.uuid4(), name="Mine", slug="mine")
        theirs = Firm(id=uuid.uuid4(), name="Theirs", slug="theirs")
        s.add_all([mine, theirs])
        await s.flush()
        hh = Household(id=uuid.uuid4(), firm_id=theirs.id, name="Their Client")
        s.add(hh)
        await s.commit()

        check("their own firm can read it",
              (await household_brain(s, hh.id, firm_id=theirs.id)) is not None, True)
        check("another firm cannot — it reads as not found",
              await household_brain(s, hh.id, firm_id=mine.id), None)
        check("an id that does not exist is also not found",
              await household_brain(s, uuid.uuid4(), firm_id=mine.id), None)
        try:
            await household_brain(s, hh.id)  # type: ignore[call-arg]
            check("omitting firm_id is refused", "allowed", "TypeError")
        except TypeError:
            check("omitting firm_id is refused", "TypeError", "TypeError")
    await engine.dispose()


async def vault_behaviour() -> None:
    """A client lists their own household's documents, and never a neighbour's."""
    from fastapi import HTTPException
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.api.vault import list_documents
    from app.core.db import Base
    from app.models.enums import UserRole
    from app.models.graph import Household, Person
    from app.models.identity import User
    from app.models.tenant import Firm
    from app.models.vault import ClientDocument

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [Base.metadata.tables[n] for n in
              ("firm", "household", "person", "app_user", "client_document")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="F", slug="f")
        s.add(firm)
        await s.flush()
        mine = Household(id=uuid.uuid4(), firm_id=firm.id, name="Mine")
        neighbour = Household(id=uuid.uuid4(), firm_id=firm.id, name="Neighbour")
        s.add_all([mine, neighbour])
        await s.flush()
        me = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=mine.id,
                    full_name="Me Client", kyc={}, profile={})
        s.add(me)
        await s.flush()
        for hh, name in ((mine, "my-statement.pdf"), (neighbour, "their-will.pdf")):
            s.add(ClientDocument(firm_id=firm.id, household_id=hh.id, uploaded_by="adviser",
                                 filename=name, doc_type="general", tags=[],
                                 is_client_visible=True, size_bytes=1))
        await s.commit()

        client = User(id=uuid.uuid4(), firm_id=firm.id, email="c@x", full_name="C",
                      hashed_password="x", role=UserRole.CLIENT, person_id=me.id)
        adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@x", full_name="A",
                       hashed_password="x", role=UserRole.ADVISER)

        own = await list_documents(household_id=mine.id, user=client, firm=firm, db=s)
        check("a client sees their own documents", [d["filename"] for d in own],
              ["my-statement.pdf"])
        try:
            leaked = await list_documents(household_id=neighbour.id, user=client, firm=firm, db=s)
            check("a client cannot list a neighbour's documents",
                  [d["filename"] for d in leaked], "404")
        except HTTPException as exc:
            check("a client cannot list a neighbour's documents", str(exc.status_code), "404")

        staff = await list_documents(household_id=neighbour.id, user=adviser, firm=firm, db=s)
        check("staff still see any household in their firm",
              [d["filename"] for d in staff], ["their-will.pdf"])
    await engine.dispose()


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "app"

    print("\n=== every household read passes a firm ===")
    missing = unscoped_calls(root)
    for m in missing:
        print(f"    unscoped: {m}")
    check("calls that omit firm_id", len(missing), 0)

    print("\n=== the definitions make it required ===")
    wrong = signatures_require_it(root)
    for w in wrong:
        print(f"    not required: {w}")
    check("definitions where firm_id is optional or absent", len(wrong), 0)

    print("\n=== behaviour ===")
    asyncio.run(behaviour())

    print("\n=== client documents stay with their own household ===")
    asyncio.run(vault_behaviour())

    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
