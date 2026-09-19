"""Seed the conflicts inventory and WSP grid (L200-7 §2.2, §3.1) for firms that predate
these tables.

seed/run.py skips a firm that already exists, so this applies to a database already in
service the same way every other *_backfill.py script does: idempotent, keyed on the
unique (firm, conflict_key) / (firm, rule_key) constraint, safe to re-run.

    python -m seed.conflicts_wsp_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.compliance_program import ConflictInventoryItem, WSPRule
from app.models.tenant import Firm

log = get_logger("aurea.seed.conflicts_wsp_backfill")

# L200-7 §2.2's conflicts-inventory table, verbatim in substance.
CONFLICTS = [
    dict(
        conflict_key="revenue_sharing",
        title="Revenue sharing / shelf payments",
        mechanism_of_harm="Product menu tilts toward payers.",
        mitigation="Menu governance ignores revenue in scoring; periodic menu-vs-payment "
                    "correlation testing.",
        disclosure="Payments disclosed in ADV / firm disclosure documents.",
        owner="Compliance",
    ),
    dict(
        conflict_key="affiliated_products",
        title="Affiliated products",
        mechanism_of_harm="Firm earns twice when clients hold house funds.",
        mitigation="Same-diligence standard documented; fee credits or exclusion from "
                    "advisory fee basis where used.",
        disclosure="Explicit disclosure with alternatives noted in the advice file.",
        owner="Research/CIO",
    ),
    dict(
        conflict_key="payout_grid_incentives",
        title="Payout-grid incentives",
        mechanism_of_harm="Comp spikes at production thresholds distort year-end "
                            "recommendations.",
        mitigation="Surveillance concentrates on threshold-proximate reps in Q4; grid "
                    "design review.",
        disclosure="Disclosure of compensation structure to clients.",
        owner="Compliance",
    ),
    dict(
        conflict_key="cash_sweep_economics",
        title="Cash sweep economics",
        mechanism_of_harm="Firm earns spread on client cash; incentive to keep cash high.",
        mitigation="Cash-drag monitoring in advisory accounts; alternatives (purchased "
                    "money funds) offered and documented.",
        disclosure="Sweep-yield disclosure at account opening and on statements.",
        owner="Operations",
    ),
    dict(
        conflict_key="rollover_capture",
        title="Rollover capture",
        mechanism_of_harm="Fees begin when plan assets become IRA assets.",
        mitigation="PTE 2020-02 comparison worksheets; sampling of rollover files; "
                    "declined-rollover documentation kept too.",
        disclosure="Comparative rationale in the advice file (rollover_pte_documenter).",
        owner="Compliance",
    ),
    dict(
        conflict_key="program_selection",
        title="Form over substance in program choice",
        mechanism_of_harm="Wrap program selected where brokerage was cheaper for the "
                            "client's activity.",
        mitigation="Program-suitability rationale at onboarding; annual wrap-vs-commission "
                    "cost analysis on low-activity accounts (reverse-churning test).",
        disclosure="Program-selection rationale retained per mandate.",
        owner="Adviser",
    ),
]

# L200-7 §3.1's WSP grid, mapped to what the platform already runs where evidence is
# genuinely automatic, and left attestation-only where it genuinely is not yet.
WSP_ROWS = [
    dict(
        rule_key="suitability.recommendation_review",
        ontology_rule_id="us.finra.2111",
        obligation="FINRA 2111 suitability — every recommendation checked against mandate",
        designated_role="Compliance (surveillance review)",
        supervisory_activity="Every Recommendation is evaluated against the active "
            "regulatory framework before it can be approved (app.compliance.rules).",
        frequency="per_event",
        evidence_description="ComplianceCheck row per recommendation, cited to a rule id "
            "and framework version.",
        evidence_automated=True,
    ),
    dict(
        rule_key="wash_sale.loss_harvest",
        ontology_rule_id="us.irc.1091_wash_sale",
        obligation="IRC §1091 — no wash sale on loss harvesting",
        designated_role="Compliance (surveillance review)",
        supervisory_activity="Order-set-level wash-sale check runs on every drift/rebalance "
            "proposal; tax_intelligence flags lot-level wash_sale_risk on harvest candidates.",
        frequency="per_event",
        evidence_description="ComplianceCheck finding + tax_intelligence wash_sale_risk flag.",
        evidence_automated=True,
    ),
    dict(
        rule_key="conduct.churning_turnover",
        ontology_rule_id="code.std1.fair_dealing",
        obligation="Fair dealing / no excessive trading (turnover surveillance)",
        designated_role="Compliance (surveillance review)",
        supervisory_activity="reverse_churning agent flags fee-paying accounts with no "
            "advisory activity; conduct_surveillance summarises open flags.",
        frequency="quarterly",
        evidence_description="SurveillanceFlag rows + conduct_surveillance run summary.",
        evidence_automated=True,
    ),
    dict(
        rule_key="aml.screening_disposition",
        ontology_rule_id="us.bsa.aml",
        obligation="BSA/OFAC — sanctions and PEP screening disposition",
        designated_role="AML Operations",
        supervisory_activity="adverse_media_pep screens every onboarding party; true "
            "matches route to compliance for four-eyes disposition.",
        frequency="per_event",
        evidence_description="OnboardingCase.screening result + disposition rationale.",
        evidence_automated=True,
    ),
    dict(
        rule_key="records.decision_recorded",
        ontology_rule_id="us.sec.204_2",
        obligation="Advisers Act 204-2 — books and records of every advice decision",
        designated_role="Compliance",
        supervisory_activity="Every decide()/rollback() call writes a hash-chained "
            "LedgerEntry — the platform's books-and-records spine.",
        frequency="per_event",
        evidence_description="LedgerEntry chain (app.provenance.ledger), verifiable end to end.",
        evidence_automated=True,
    ),
    dict(
        rule_key="marketing.advertising_review",
        ontology_rule_id=None,
        obligation="Marketing Rule / FINRA 2210 — pre-use review of client-facing "
            "performance and marketing content",
        designated_role="Compliance",
        supervisory_activity="Manual principal pre-review of client reports and marketing "
            "material before use — no automated pre-review pipeline exists yet.",
        frequency="per_event",
        evidence_description="Signed-off review note attached to the content record.",
        evidence_automated=False,
    ),
    dict(
        rule_key="correspondence.off_channel_sampling",
        ontology_rule_id=None,
        obligation="FINRA 3110(b)(4) — risk-based sampling of correspondence, including "
            "off-channel communications capture completeness",
        designated_role="Compliance",
        supervisory_activity="Periodic manual sampling of adviser-client Message records "
            "and attestations that no off-channel communication occurred.",
        frequency="monthly",
        evidence_description="Sampling log and attestation record (kept outside the platform "
            "today).",
        evidence_automated=False,
    ),
    dict(
        rule_key="licensing.ce_registration",
        ontology_rule_id=None,
        obligation="U4/U5 amendments, CE lapses, and state registration currency",
        designated_role="Operations",
        supervisory_activity="Manual tracking of licensing/CE status per adviser — no "
            "licensing-clock dashboard or trade-blocking-on-lapse control exists yet.",
        frequency="quarterly",
        evidence_description="Spreadsheet-based licensing roster (kept outside the platform "
            "today).",
        evidence_automated=False,
    ),
]


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        conflicts_created = wsp_created = 0

        for firm in firms:
            existing_conflicts = {
                c.conflict_key for c in (await s.execute(
                    select(ConflictInventoryItem).where(ConflictInventoryItem.firm_id == firm.id)
                )).scalars().all()
            }
            for spec in CONFLICTS:
                if spec["conflict_key"] in existing_conflicts:
                    continue
                s.add(ConflictInventoryItem(firm_id=firm.id, status="active", **spec))
                conflicts_created += 1
                log.info("conflict_seeded", firm=firm.slug, conflict_key=spec["conflict_key"])

            existing_wsp = {
                r.rule_key for r in (await s.execute(
                    select(WSPRule).where(WSPRule.firm_id == firm.id)
                )).scalars().all()
            }
            for spec in WSP_ROWS:
                if spec["rule_key"] in existing_wsp:
                    continue
                s.add(WSPRule(firm_id=firm.id, is_active=True, change_log=[], **spec))
                wsp_created += 1
                log.info("wsp_rule_seeded", firm=firm.slug, rule_key=spec["rule_key"])

        await s.commit()
        print(f"\nSeeded {conflicts_created} conflict-inventory item(s) and "
              f"{wsp_created} WSP grid row(s) across {len(firms)} firm(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
