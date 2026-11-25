"""A simulated news source (B-224), standing in for a licensed news and company-data service.

It plugs into the real persona feature's seam, `aurea_core/news_sources.register`, and answers only for a firm that
opted in (the demonstration firm, slug "demo"). Its articles are invented, from invented publishers, about the demo
firm's invented prospects and clients only (`articles.py`); each says it is simulated.

Seeding opts the firm in and, where compliance has not set the persona control itself, switches profiling on with the
basis "Simulated for demonstration", marked in `firm.settings["simulated_controls"]["news_source"]` so discarding
takes back exactly what it set. No audit entry is written, because it could not be taken away; a real basis is
recorded by compliance through `set_agent_control`, on the audit log.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.aurea_core import news_sources
from app.aurea_core.introductions import plain
from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag
from app.aurea_core import markets
from app.simulations.news_source.articles import ARTICLES, ARTICLES_IN, url

KEY = "news_source"
BASIS = "Simulated for demonstration"
CONTROL = {"persona_profiling": True, "persona_basis": BASIS}


class SimulatedNews:
    key = tag(KEY)
    name = "Simulated news (invented articles)"
    simulated = True

    def applies_to(self, firm) -> bool:
        return enabled_for(firm, KEY)

    async def articles(self, firm, *, name: str, company: str | None = None) -> list[news_sources.Article]:
        from app.aurea_core import firm_time

        today = firm_time.today(firm)
        want = plain(name.split(",")[0])
        out = []
        market = markets.for_firm(firm)
        for who, items in (ARTICLES_IN if market is not None and market.code == "IN" else ARTICLES).items():
            if plain(who) != want:
                continue
            for days, publisher, title, slug, text in items:
                out.append(news_sources.Article(title=title, publisher=publisher, published_on=today - timedelta(days=days),
                                                url=url(slug), text=text, source=self.key, simulated=True))
        return out


def install() -> None:
    news_sources.register(SimulatedNews())


async def firms(session) -> list:
    from app.models.tenant import Firm

    return list((await session.execute(select(Firm).where(Firm.slug.in_(("demo", "india-demo"))))).scalars().all())


async def seed(session, firm) -> dict:
    await opt_in(session, firm, KEY)
    s = dict(firm.settings or {})
    f = dict(s.get("foundation") or {})
    marks = dict(s.get("simulated_controls") or {})
    control = "left as compliance set it"
    if KEY in marks:
        control = "already on"
    elif "persona_profiling" not in f and "persona_basis" not in f:
        f.update(CONTROL)
        marks[KEY] = dict(CONTROL)
        s["foundation"], s["simulated_controls"] = f, marks
        firm.settings = s
        flag_modified(firm, "settings")
        await session.flush()
        control = "switched on, basis simulated"
    pool = ARTICLES_IN if markets.code(firm.jurisdiction) == "IN" else ARTICLES
    return {"people_with_articles": len(pool), "articles": sum(len(v) for v in pool.values()), "control": control}


async def discard(session, firm) -> dict:
    s = dict(firm.settings or {})
    marks = dict(s.get("simulated_controls") or {})
    mark = marks.pop(KEY, None)
    removed = 0
    if mark is not None:
        f = dict(s.get("foundation") or {})
        for k, v in mark.items():
            if f.get(k) == v:       # only what it set and nobody has changed since
                f.pop(k, None)
                removed += 1
        if f:
            s["foundation"] = f
        else:
            s.pop("foundation", None)
        if marks:
            s["simulated_controls"] = marks
        else:
            s.pop("simulated_controls", None)
        firm.settings = s
        flag_modified(firm, "settings")
        await session.flush()
    was = enabled_for(firm, KEY)
    if was:
        await opt_out(session, firm, KEY)
    return {"control_settings": removed, "opt_in": int(was)}


register(Simulation(
    key=KEY, name="Simulated news source", stands_for="A licensed news and company-data service",
    seam="aurea_core.news_sources.register (the persona from public sources, B-202)",
    install=install, seed=seed, discard=discard, firms=firms,
    notes=["Invented articles about the demo firm's invented people only; publishers are invented and marked simulated.",
           "Switches the persona control on with the basis 'Simulated for demonstration' unless compliance set it."],
))
