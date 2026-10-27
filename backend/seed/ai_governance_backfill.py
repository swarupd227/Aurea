"""Seed the AI use-case inventory (L200-8 §8) from the platform's real agent and tool
catalogue, for firms that predate these tables — every agent and the highest-stakes
conversational tools get a named owner and a risk tier, not an empty register.

Idempotent: skips a firm that already has any AIUseCase row.

    python -m seed.ai_governance_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.ai_governance import AIUseCase
from app.models.tenant import Firm

log = get_logger("aurea.seed.ai_governance_backfill")

# (use_case_key, name, category, owner, risk_tier, description)
_AGENTS = [
    ("drift_rebalancing", "Drift & Tax-Managed Rebalancing", "agent", "Portfolio team lead", "high",
     "Drafts and, on approval, executes real orders against a client's book — the platform's one book-moving agent."),
    ("onboarding_kyc_aml", "Onboarding · KYC/AML", "agent", "Head of Operations", "high",
     "Extracts onboarding documents, screens applicants, drafts suitability and mandate on approval."),
    ("adverse_media_pep", "Adverse Media & PEP Screening", "agent", "AML Officer", "high",
     "Screens onboarding parties against sanctions/PEP/adverse-media feeds; a true match has real regulatory consequence."),
    ("cip_identity_verifier", "CIP Identity Verification", "agent", "AML Officer", "high",
     "Runs KYC identity verification via the identity adapter — a wrong pass/fail is a CIP failure."),
    ("custodian_account_opener", "Custodian Account Opener", "agent", "Head of Operations", "high",
     "Pushes an approved onboarding case to the custodian's account-opening API — moves real account state."),
    ("conduct_surveillance", "Conduct Surveillance", "agent", "Compliance Officer", "high",
     "Supervisory summary over open surveillance flags — the auto-pause kill-switch depends on its findings."),
    ("reverse_churning", "Reverse-Churning Detection", "agent", "Compliance Officer", "high",
     "Flags fee-paying accounts with no advisory activity — a named exam-priority pattern."),
    ("book_integration", "Book Integration", "agent", "Head of Operations", "medium",
     "Reconciles an acquired book's inbound feed against the client brain and commits accepted mappings."),
    ("research_reporting", "Research & Reporting", "agent", "Head of Advice", "medium",
     "Drafts a client-ready ClientReport — client-facing content under the Marketing Rule's review lineage."),
    ("next_best_action", "Next-Best-Action", "agent", "Head of Advice", "medium",
     "Firm-wide multi-signal scan surfacing prioritised opportunities and risks."),
    ("client_care", "Client Care", "agent", "Head of Advice", "medium",
     "Detects at-risk/relationship signals and proposes proactive outreach."),
    ("estate_succession", "Estate & Succession", "agent", "Head of Advice", "medium",
     "Maps the family wealth/trust/heir picture and surfaces succession gaps."),
    ("tax_intelligence", "Tax Intelligence", "agent", "Head of Advice", "medium",
     "Cross-book tax optimisation — loss harvesting, wash-sale flags, RMD alerts."),
    ("ips_drafting", "IPS Drafting", "agent", "Head of Advice", "medium",
     "Drafts the Investment Policy Statement from the client brain."),
    ("asset_location", "Asset Location", "agent", "Head of Advice", "medium",
     "Detects tax-inefficient asset placement across account types."),
    ("glide_path", "Glide Path", "agent", "Head of Advice", "medium",
     "Recommends equity step-down as a household approaches a major goal date."),
    ("edd_sow_narrator", "EDD Source-of-Wealth Narrator", "agent", "AML Officer", "medium",
     "Synthesises a source-of-wealth memo with a corroboration-gap checklist for Medium/High AML risk clients."),
    ("rollover_pte_documenter", "Rollover PTE Documenter", "agent", "Compliance Officer", "medium",
     "Generates DOL PTE 2020-02 best-interest documentation for retirement rollovers."),
    ("nigo_prevention", "NIGO Prevention", "agent", "Head of Operations", "medium",
     "Validates uploaded onboarding documents against the registration-type matrix."),
    ("meeting_prep", "Meeting Prep", "agent", "Head of Advice", "low",
     "Assembles a pre-meeting brief — read-only synthesis of existing data."),
    ("meeting_companion", "Meeting Companion", "agent", "Head of Advice", "low",
     "Turns a meeting transcript into structured notes and proposed action items."),
    ("behavioural_finance", "Behavioural Finance", "agent", "Head of Advice", "low",
     "Builds a cognitive-bias profile from transcripts/messages for adviser coaching."),
    ("regulatory_countdown", "Regulatory Countdown", "agent", "Compliance Officer", "low",
     "Jurisdiction-aware deadline alerts — informational only."),
    ("wallet_share_scout", "Wallet Share Scout", "agent", "Head of Advice", "low",
     "Surfaces held-away consolidation opportunities with talking points."),
    ("abandonment_recovery", "Abandonment Recovery", "agent", "Head of Operations", "low",
     "Detects stalled onboarding cases and drafts an adviser nudge."),
    ("billing", "Billing", "agent", "Head of Operations", "high",
     "Works out each fee, proposes the billing run, and on approval issues bills and takes them from client cash — a wrong fee is a client reimbursement and a named exam topic."),
]

_TOOLS = [
    ("execute_orders", "Execute Orders (tool)", "tool", "Portfolio team lead", "high",
     "The conversational path to real order placement and settlement — pauses for confirmation, but the highest-stakes tool in the catalogue."),
    ("decide_recommendation", "Approve Recommendation (tool)", "tool", "Portfolio team lead", "high",
     "Approves/modifies/dismisses agent recommendations via the same exactly-once path as the Studio review page."),
    ("post_corporate_action_entitlement", "Post Corporate Action Entitlement (tool)", "tool",
     "Head of Operations", "high",
     "Applies cash/holding/tax-lot arithmetic for a corporate action — cannot be undone or re-posted."),
    ("credit_invoice", "Credit Invoice (tool)", "tool", "Head of Operations", "high",
     "Offsets an issued invoice in full and returns any fee already taken to the client's cash."),
    ("record_valuation", "Record Valuation (tool)", "tool", "Head of Operations", "high",
     "Records a manager's stated value for a private holding - revalues every holding of it, which changes client balances and the fees charged on them."),
    ("run_billing", "Prepare Billing Run (tool)", "tool", "Head of Operations", "medium",
     "Has the billing agent draft a run for approval; nothing is issued or taken until it is approved."),
    ("run_agent", "Run Agent (tool)", "tool", "Head of Advice", "medium",
     "Starts an agent from a sentence after a confirmation that names its autonomy tier. The agent proposes and never approves its own proposal; an agent that would move a client's book or cash stops at a proposal whatever its tier."),
]


async def backfill() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        created = 0

        for firm in firms:
            have = set((await s.execute(
                select(AIUseCase.use_case_key).where(AIUseCase.firm_id == firm.id))).scalars().all())
            added = 0
            for key, name, category, owner, risk_tier, description in _AGENTS + _TOOLS:
                if key in have:
                    continue
                s.add(AIUseCase(
                    firm_id=firm.id, use_case_key=key, name=name, category=category, owner=owner,
                    risk_tier=risk_tier, description=description, status="active",
                ))
                created += 1
                added += 1
            if added:
                log.info("ai_use_cases_seeded", firm=firm.slug, count=added)

        await s.commit()
        print(f"\nSeeded {created} AI use-case inventory row(s) across {len(firms)} firm(s).\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(backfill())
