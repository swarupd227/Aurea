"""A simulated KYC registry lookup, standing in for a KRA's and the Central KYC registry's status services (B-386).

The real feature is `aurea_core/kyc_registry.py`: the registry status beside the account's own state, its reasons, remediation and
book view. It reads only what a person recorded or what a provider registered at `kyc_registry.register` returns; this package is only
that provider. For a firm that opted in, a person's lookup (behind a Confirm card) returns an invented answer, recorded as a row
tagged `simulated:kyc_registry` and shown with a "simulated" chip. It reaches no registry and is not a KYC check.

The answer repeats what already stands for a party; otherwise it is scripted for two demonstration names and, for anyone else, picked
from the name, so the same person always gets the same answer. Seeding only opts the firm in. Discarding deletes every lookup it
answered, the steps recorded against them and the tasks they raised, and the opt-in; attempts to reach a client stay.
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select

from app.aurea_core import firm_time, kyc_registry
from app.models.engagement import Task
from app.models.kyc_registry import KycFollowUp, KycLookup
from app.models.tenant import Firm
from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "kyc_registry"
SOURCE = tag(KEY)
NAME = "Simulated KYC registry lookup (demonstration)"
# name -> (status, deficiencies): the demonstration's own cases
SCRIPT = {"nandini kapoor": ("on_hold", ["mobile_not_verified", "email_not_verified"]), "subir banerjee": ("registered", [])}
# the rest, by the name: mostly validated, as the module says of a clean resident
SPREAD = (("validated", []), ) * 7 + (("registered", []), ("on_hold", ["pan_aadhaar_not_linked"]), ("not_found", []))


def _n(*parts, mod: int) -> int:
    return uuid.uuid5(uuid.NAMESPACE_URL, ":".join(str(p) for p in parts)).int % mod


class SimulatedRegistry:
    key = SOURCE
    name = NAME
    simulated = True

    def enabled_for(self, firm) -> bool:
        return enabled_for(firm, KEY) and kyc_registry.applies(firm)

    async def lookup(self, session, firm, *, name: str, holder=None, case=None) -> dict:
        wanted = " ".join(name.split()).casefold()
        standing = None
        if holder is not None:
            everyone = await kyc_registry.standing(session, firm.id)
            standing = everyone.get(("person", holder.id)) or everyone.get(("entity", holder.id))
        if wanted in SCRIPT:
            status, reasons = SCRIPT[wanted]
        elif standing is not None:
            status = standing["status"]
            reasons = standing["deficiencies"] or (["not_stated"] if status in kyc_registry.STOPS else [])
        else:
            status, reasons = SPREAD[_n(wanted, mod=len(SPREAD))]
        today = firm_time.today(firm)
        return {"registry": "kra", "status": status, "agency": "Simulated KRA", "deficiencies": list(reasons),
                "reference": f"SIM-KRA-{today.strftime('%Y%m%d')}-{_n(wanted, today, mod=10**6):06d}", "answered_on": today,
                "holds": ["identity_proof", "address_proof", "pan_card", "photograph"] if status == "validated" else []}


PROVIDER = SimulatedRegistry()


def install() -> None:
    kyc_registry.register(PROVIDER)


async def demo_firms(session) -> list:
    return list((await session.execute(select(Firm).where(Firm.slug == "india-demo"))).scalars().all())


async def seed(session, firm) -> dict:
    if not kyc_registry.applies(firm):
        return {"opted_in": False, "skipped": "not an Indian firm"}
    if kyc_registry.PROVIDERS.get(SOURCE) is not PROVIDER:
        install()
    await opt_in(session, firm, KEY)
    return {"opted_in": True}


async def discard(session, firm) -> dict:
    gone = {}
    for label, model, column in (("steps", KycFollowUp, KycFollowUp.source_system), ("lookups", KycLookup, KycLookup.source_system),
                                 ("tasks", Task, Task.source)):
        where = (model.firm_id == firm.id, column == SOURCE)
        gone[label] = (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()
        if gone[label]:
            await session.execute(delete(model).where(*where))
    was = enabled_for(firm, KEY)
    if was:
        await opt_out(session, firm, KEY)
    await session.flush()
    return {**gone, "opted_out": was}


register(Simulation(
    key=KEY, name="Simulated KYC registry lookup", stands_for="A KRA's and the Central KYC registry's status lookup (through a registered intermediary)",
    seam="aurea_core.kyc_registry.register: an answer a person asks for behind a Confirm card, recorded as the registry status (B-386)",
    install=install, seed=seed, discard=discard, firms=demo_firms,
    notes=["Answers are invented, recorded only when a person asks, tagged simulated:kyc_registry and chipped; seeding only opts in."],
))
