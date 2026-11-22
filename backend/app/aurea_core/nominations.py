"""Nominations on Indian accounts, and how they sit against a will (India row 5).

A nominee on a demat account or a mutual fund folio is recorded as the account's beneficiary. SEBI caps them at three
from 1 September 2026 (circular of 29 May 2026, as reproduced; SEBI's own PDF was not opened), so a fourth is refused.
Bank accounts (four) and EPF (family only) are not accounts here. An opt-out declaration is not modelled: no
nomination on file says so, and does not mean the client opted out.

A nomination is not inheritance. The Supreme Court held in Shakti Yezdani (2023 INSC 1076) that a nominee does not
take over succession law, so a nominee the will does not name holds for the legal heirs. Where a will on file names its
heirs, a nominee outside them is flagged for the adviser; nothing is changed. Insurance differs (Insurance Act s.39(7)),
which aurea_core/protection.payee_of says, and is not compared here.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import markets
from app.models.graph import Account, AccountBeneficiary
from app.models.tenant import Firm

CAPS = {"IN": 3}
CITATION = "Shakti Yezdani v Jayanand Salgaonkar, 2023 INSC 1076"


def cap_for(firm: Firm | None) -> int | None:
    market = markets.for_firm(firm) if firm else None
    return CAPS.get(market.code) if market else None


async def refusal(session: AsyncSession, firm: Firm, account: Account) -> str | None:
    """Why one more nominee may not be added to this account, or None."""
    cap = cap_for(firm)
    if cap is None:
        return None
    held = (await session.execute(select(func.count(AccountBeneficiary.id)).where(
        AccountBeneficiary.account_id == account.id))).scalar_one()
    if held >= cap:
        return (f"{account.name} already has {held} nominee(s), and a demat account or a fund folio takes at most "
                f"{cap} (SEBI, from 1 September 2026).")
    return None


def will_issues(account_name: str, nominees: list[str], heirs: list[str] | None) -> list[dict]:
    """A nominee the owner's will does not name. Nothing when the will names no heirs on file."""
    if not heirs:
        return []
    named = {h.strip().lower() for h in heirs}
    return [{"account": account_name, "name": n, "issue": "nominee is not named in the will: a nominee holds for the "
             f"legal heirs ({CITATION})"} for n in nominees if n.strip().lower() not in named]
