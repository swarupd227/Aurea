"""Nominations on Indian accounts, and how they sit against a will (India row 5).

A nominee on a demat account or a mutual fund folio is recorded as the account's beneficiary. SEBI caps them at three
from 1 September 2026 (circular of 29 May 2026, as reproduced; SEBI's own PDF was not opened), so a fourth is refused.
Bank accounts (four) and EPF (family only) are not accounts here: a person records each from its document, with a signed opt-out
for a demat account or a folio (`india_estate.py`), and `check` reads them. No nomination and no opt-out on file says so, and does
not mean the client opted out.

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


# ── Nominations the platform does not hold as accounts (India row 13, B-232) ────────────────────────────────────────────
# A demat account or a folio is an account here, with its nominees on AccountBeneficiary. A bank account's nominees, EPF's and a
# signed opt-out are recorded by a person from the document (`india_estate.record_nomination`) and read here. Everything below
# reports and changes nothing: a nominee is the client's choice and a form signed with the bank, the depository or the PF office.
KINDS = {"demat": "Demat account", "mutual_fund": "Mutual fund folio", "bank_account": "Bank account", "epf": "EPF"}
OPT_OUT_KINDS = ("demat", "mutual_fund")        # a signed opt-out is a SEBI choice for these two
FAMILY_RELATIONS = frozenset({"spouse", "wife", "husband", "son", "daughter", "child", "father", "mother", "parent", "brother", "sister",
                              "widow", "grandchild", "dependent parent", "dependent child"})
EPF_NOTE = "EPF takes a member of the member's family"


def cap_of_kind(kind: str, today) -> int | None:
    from app.aurea_core import law_figures

    key = {"demat": "in_nominee_cap_demat_mf", "mutual_fund": "in_nominee_cap_demat_mf", "bank_account": "in_nominee_cap_bank"}.get(kind)
    return int(law_figures.value(key, today)) if key else None


def check(records: list[dict], *, today, gone: set[str] | None = None, heirs: dict | None = None) -> list[dict]:
    """What the recorded nominations do not satisfy, each with its reason. `records` are the latest per asset; `gone` is who has died
    (lower case); `heirs` maps a person's id to who their will names. Flags only."""
    out = []
    gone = {g.strip().lower() for g in (gone or set())}
    for r in records:
        kind, label = r["asset_kind"], r["label"]
        nominees = r.get("nominees") or []
        cap = cap_of_kind(kind, today)
        if r["status"] == "opted_out":
            if kind not in OPT_OUT_KINDS:
                out.append({"account": label, "name": None, "issue": f"an opt-out is not recorded for a {KINDS[kind].lower()}"})
            elif not r.get("declaration_on"):
                out.append({"account": label, "name": None, "issue": "opted out, with no signed declaration dated on file"})
            continue
        if not nominees:
            out.append({"account": label, "name": None, "issue": "marked as nominated, with no nominee named"})
            continue
        if cap is not None and len(nominees) > cap:
            out.append({"account": label, "name": None, "issue": f"{len(nominees)} nominees, and a {KINDS[kind].lower()} takes at most {cap}"})
        shares = [n.get("share") for n in nominees]
        if all(s is not None for s in shares) and abs(sum(float(s) for s in shares) - 100) > 0.01:
            out.append({"account": label, "name": None, "issue": f"the shares add to {sum(float(s) for s in shares):g}%, not 100%"})
        for n in nominees:
            who = (n.get("name") or "").strip()
            if who.lower() in gone:
                out.append({"account": label, "name": who, "issue": "nominee has died"})
            if kind == "epf" and (n.get("relationship") or "").strip().lower() not in FAMILY_RELATIONS:
                out.append({"account": label, "name": who,
                            "issue": f"{EPF_NOTE}, and {n.get('relationship') or 'no relationship'} is not a family relationship on the list"})
        out += will_issues(label, [n["name"] for n in nominees if n.get("name")], (heirs or {}).get(r.get("person_id")))
    return out
