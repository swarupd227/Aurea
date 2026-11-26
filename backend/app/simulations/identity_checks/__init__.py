"""A simulated PAN verification, penny-drop and video verification service (B-387).

The real feature is `aurea_core/identity_checks.py`: the attempts, their outcomes, the scored name match, the retries, the
alternative method and the human queue. It reads only what a provider registered at `identity_checks.register` answers; this
package is only that provider. For a firm that opted in, a person's check (behind a Confirm card) gets an invented answer, recorded
as a row tagged `simulated:identity_checks` and shown with a "simulated" chip. It reaches no PAN database, no bank and no video
service, and is not a verification of anyone.

So that every failure path can be shown, answers are scripted:

    bank    an account ending 0000 does not exist; ending 1111 shows the name in initials; ending 2222 shows another name; ending 3333
            gets no answer; any other shows the registered bank's owner, else the party's name
    pan     a PAN ending Z is inoperative; any other is valid in the party's name
    vipv    the demonstration prospect's first attempt fails the liveness check and the next passes; anyone else passes

Seeding only opts the firm in. Discarding deletes every attempt it answered and the opt-in; results a person recorded by hand stay.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select

from app.aurea_core import identity_checks
from app.models.identity_checks import VerificationAttempt
from app.models.tenant import Firm
from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "identity_checks"
SOURCE = tag(KEY)
NAME = "Simulated PAN, penny-drop and video checks (demonstration)"
FAILS_FIRST = frozenset({"devendra rao"})


class SimulatedChecks:
    key = SOURCE
    name = NAME
    simulated = True

    def enabled_for(self, firm) -> bool:
        return enabled_for(firm, KEY) and identity_checks.applies(firm)

    async def check(self, session, firm, *, kind: str, name: str, attempt: int, bank_link=None, last4: str | None = None, pan: str | None = None) -> dict:
        ref = f"SIM-{kind.upper()}-{uuid.uuid5(uuid.NAMESPACE_URL, f'{name}:{kind}:{last4}:{attempt}').int % 10**8:08d}"
        if kind == "vipv":
            if " ".join(name.split()).casefold() in FAILS_FIRST and attempt == 1:
                return {"result": "failed", "reason": "liveness_failed", "reference": ref}
            return {"result": "ok", "reference": ref}
        if kind == "pan":
            if (pan or "").endswith("Z"):
                return {"result": "failed", "reason": "pan_inoperative", "reference": ref}
            return {"result": "ok", "name": name, "reference": ref}
        if last4 == "0000":
            return {"result": "failed", "reason": "account_not_found", "reference": ref}
        if last4 == "3333":
            return {"result": "unavailable", "reference": ref}
        words = name.split()
        if last4 == "1111" and len(words) > 1:
            return {"result": "ok", "name": f"{words[0][0]} {words[-1]}", "reference": ref}
        if last4 == "2222":
            return {"result": "ok", "name": "A Different Holder", "reference": ref}
        return {"result": "ok", "name": bank_link.owner_name if bank_link is not None else name, "reference": ref}


PROVIDER = SimulatedChecks()


def install() -> None:
    identity_checks.register(PROVIDER)


async def demo_firms(session) -> list:
    return list((await session.execute(select(Firm).where(Firm.slug == "india-demo"))).scalars().all())


async def seed(session, firm) -> dict:
    if not identity_checks.applies(firm):
        return {"opted_in": False, "skipped": "not an Indian firm"}
    if identity_checks.PROVIDERS.get(SOURCE) is not PROVIDER:
        install()
    await opt_in(session, firm, KEY)
    return {"opted_in": True}


async def discard(session, firm) -> dict:
    where = (VerificationAttempt.firm_id == firm.id, VerificationAttempt.source_system == SOURCE)
    rows = (await session.execute(select(func.count()).select_from(VerificationAttempt).where(*where))).scalar_one()
    if rows:
        await session.execute(delete(VerificationAttempt).where(*where))
    was = enabled_for(firm, KEY)
    if was:
        await opt_out(session, firm, KEY)
    await session.flush()
    return {"attempts": rows, "opted_out": was}


register(Simulation(
    key=KEY, name="Simulated identity and bank checks", stands_for="A PAN verification service, a penny-drop service and a video in-person verification provider",
    seam="aurea_core.identity_checks.register: one attempt a person asks for behind a Confirm card, recorded with its outcome (B-387)",
    install=install, seed=seed, discard=discard, firms=demo_firms,
    notes=["Answers are invented and scripted so every failure path can be shown; recorded only when a person asks, tagged simulated:identity_checks and chipped."],
))
