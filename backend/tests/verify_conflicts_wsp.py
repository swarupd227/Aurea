"""Prove the conflicts inventory + WSP grid analytics (L200-7 §2.2, §3.1, §10) compute
what the module's own metric catalog asks for: which conflicts are overdue for review, and
whether an obligation's evidence is automated and current against its own stated frequency.

Runs against a throwaway SQLite database so it needs no cloud connection.

    python -m tests.verify_conflicts_wsp
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.aurea_core import compliance_program as engine
from app.core.db import Base, utcnow
from app.models.compliance_program import ConflictInventoryItem, WSPRule
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    wanted = ["firm", "conflict_inventory_item", "wsp_rule"]
    tables = [Base.metadata.tables[n] for n in wanted]
    async with db.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    Session = async_sessionmaker(db, expire_on_commit=False)

    async with Session() as s:
        firm = Firm(id=uuid.uuid4(), name="Test", slug="test")
        s.add(firm)
        await s.flush()

        now = utcnow()

        # One conflict reviewed long ago (overdue against its own cadence), one current.
        s.add_all([
            ConflictInventoryItem(
                firm_id=firm.id, conflict_key="revenue_sharing", title="Revenue sharing",
                mechanism_of_harm="x", mitigation="x", disclosure="x", status="active",
                review_cadence_days=365, last_reviewed_at=now - timedelta(days=400),
            ),
            ConflictInventoryItem(
                firm_id=firm.id, conflict_key="cash_sweep_economics", title="Cash sweep",
                mechanism_of_harm="x", mitigation="x", disclosure="x", status="active",
                review_cadence_days=365, last_reviewed_at=now - timedelta(days=10),
            ),
            # A retired conflict past its cadence must not count as overdue — it is no
            # longer live, so there is nothing to re-review.
            ConflictInventoryItem(
                firm_id=firm.id, conflict_key="legacy_conflict", title="Retired conflict",
                mechanism_of_harm="x", mitigation="x", disclosure="x", status="retired",
                review_cadence_days=365, last_reviewed_at=now - timedelta(days=1000),
            ),
        ])

        # WSP rows: one automated & current, one automated but stale, one manual
        # (attestation-only), one per_event with no evidence yet (never "stale" on a clock).
        s.add_all([
            WSPRule(
                firm_id=firm.id, rule_key="wash_sale.loss_harvest", obligation="IRC 1091",
                designated_role="Compliance", supervisory_activity="x", frequency="monthly",
                evidence_description="ComplianceCheck", evidence_automated=True,
                last_evidence_at=now - timedelta(days=5), is_active=True, change_log=[],
            ),
            WSPRule(
                firm_id=firm.id, rule_key="suitability.recommendation_review",
                obligation="FINRA 2111", designated_role="Compliance",
                supervisory_activity="x", frequency="monthly",
                evidence_description="ComplianceCheck", evidence_automated=True,
                last_evidence_at=now - timedelta(days=90), is_active=True, change_log=[],
            ),
            WSPRule(
                firm_id=firm.id, rule_key="marketing.advertising_review",
                obligation="Marketing Rule", designated_role="Compliance",
                supervisory_activity="x", frequency="quarterly",
                evidence_description="Signed review note", evidence_automated=False,
                last_evidence_at=None, is_active=True, change_log=[],
            ),
            WSPRule(
                firm_id=firm.id, rule_key="aml.screening_disposition",
                obligation="BSA/OFAC", designated_role="AML Operations",
                supervisory_activity="x", frequency="per_event",
                evidence_description="OnboardingCase.screening", evidence_automated=True,
                last_evidence_at=None, is_active=True, change_log=[],
            ),
            # Inactive rows must not count toward the coverage denominator at all.
            WSPRule(
                firm_id=firm.id, rule_key="retired_obligation", obligation="Retired",
                designated_role="Compliance", supervisory_activity="x", frequency="monthly",
                evidence_description="x", evidence_automated=False,
                last_evidence_at=None, is_active=False, change_log=[],
            ),
        ])
        await s.flush()

        print("\n=== conflicts_summary: status counts and overdue review ===")
        cs = await engine.conflicts_summary(s, firm.id)
        check("total conflicts", cs["total"], 3)
        check("active conflicts", cs["active"], 2)
        check("retired conflicts", cs["retired"], 1)
        overdue_keys = sorted(r["conflict_key"] for r in cs["overdue_review"])
        check("overdue review names only the stale active one", overdue_keys, ["revenue_sharing"])
        check("retired conflict never counted as overdue",
              "legacy_conflict" in overdue_keys, False)

        print("\n=== evidence_coverage: automated vs. manual, and staleness against frequency ===")
        ec = await engine.evidence_coverage(s, firm.id)
        check("active WSP rows counted (inactive excluded)", ec["total_rules"], 4)
        check("automated evidence count", ec["automated_evidence"], 3)
        check("manual attestation count", ec["manual_attestation"], 1)
        check("coverage pct", ec["coverage_pct"], 75.0)
        stale_keys = sorted(r["rule_key"] for r in ec["stale_evidence"])
        # suitability.recommendation_review: monthly, last evidenced 90 days ago -> stale.
        # marketing.advertising_review: quarterly, never evidenced -> stale.
        # aml.screening_disposition: per_event -> never flagged stale on a clock.
        # wash_sale.loss_harvest: monthly, evidenced 5 days ago -> current.
        check("stale evidence names the right rows", stale_keys,
              ["marketing.advertising_review", "suitability.recommendation_review"])
        check("per_event row never flagged stale",
              "aml.screening_disposition" in stale_keys, False)
        check("current automated row not flagged stale",
              "wash_sale.loss_harvest" in stale_keys, False)

    await db.dispose()  # otherwise the process hangs on the open pool at exit
    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
