"""Recheck the open over-promising flags against the corrected rule (B-48).

The rule used to match words, so a disclaimer ("returns are not guaranteed") raised a flag; on 3 Nov s22 had failed 637
times in 90 days. The corrected rule (`compliance/rules.promises`) reads a denial as one. The flags it raised stay open,
because only a person closes a flag: this lists, firm by firm, how many would now pass, and closes nothing unless a
compliance person is named, in which case only those are resolved, in that person's name, with the reason, on the
ledger (`provenance/supervision.resolve`). A flag whose text still promises stays open either way.

    python -m seed.overpromise_recheck                              # count only
    python -m seed.overpromise_recheck --resolve-as compliance@...  # resolve the ones that now pass
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MARK = "Over-promising language:"
NOTE = ("Rechecked against the corrected over-promising rule (B-48): the text denies an outcome rather than promising "
        "one, so it no longer fails.")


async def recheck(session: AsyncSession, firm, *, resolve_as=None) -> dict:
    from app.compliance.engine import build_context
    from app.compliance.rules import promises
    from app.models.governance import Recommendation, SurveillanceFlag
    from app.provenance import supervision

    flags = (await session.execute(select(SurveillanceFlag).where(
        SurveillanceFlag.firm_id == firm.id, SurveillanceFlag.resolved.is_(False),
        SurveillanceFlag.finding.contains(MARK)))).scalars().all()
    clear, still = [], []
    for f in flags:
        rec = await session.get(Recommendation, f.recommendation_id) if f.recommendation_id else None
        if rec is None:
            still.append(f)  # nothing to recheck it against: a person decides
            continue
        (still if promises(build_context(rec, {}).text) else clear).append(f)
    resolved = 0
    if resolve_as is not None:
        for f in clear:
            await supervision.resolve(session, f, note=NOTE, by=resolve_as.email)
            resolved += 1
    return {"firm": firm.name, "open": len(flags), "now_pass": len(clear), "still_fail": len(still), "resolved": resolved}


async def _main(resolve_as_email: str | None) -> None:
    from app.core.db import SessionLocal
    from app.models.enums import UserRole
    from app.models.identity import User
    from app.models.tenant import Firm

    async with SessionLocal() as s:
        for firm in (await s.execute(select(Firm))).scalars().all():
            who = None
            if resolve_as_email:
                who = (await s.execute(select(User).where(User.firm_id == firm.id, User.email == resolve_as_email))).scalar_one_or_none()
                if who is not None and UserRole(who.role) != UserRole.COMPLIANCE:
                    raise SystemExit(f"{resolve_as_email} is not compliance at {firm.name}; only compliance closes these flags.")
            out = await recheck(s, firm, resolve_as=who)
            if out["open"]:
                print(f"{out['firm']}: {out['open']} open over-promising flag(s), {out['now_pass']} pass the corrected rule, "
                      f"{out['still_fail']} still fail" + (f"; resolved {out['resolved']} as {resolve_as_email}" if who else ""))
        await s.commit()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--resolve-as", help="A compliance person's email; resolves, in their name, the flags that now pass")
    asyncio.run(_main(p.parse_args().resolve_as))
