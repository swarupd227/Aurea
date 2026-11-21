"""A persona for a client or prospect, from what professional and public sources published about them (B-202).

What it is: talking points, at work and beyond, each citing the article it comes from (title, publisher, date, link)
and quoting the words it rests on, dated, and good for `EXPIRY_DAYS` from the article's date. It is a prompt for a
conversation, never a judgement, and it is never shown to a client.

What it never is:

-   Anything about health, religion or belief, political opinions, sex life or sexual orientation, racial or ethnic
    origin, trade union membership, or criminal matters (`EXCLUDED`). A sentence that touches one is removed before any
    model sees the article, and a point that touches one is removed again afterwards. What was left out is a count,
    by category, and never its words.
-   Adverse media. An article about a lawsuit, a fine, an investigation or an insolvency is not a talking point: it is
    screening's and compliance's (`aml_review.py`), and is left out whole and counted.
-   Worked out. A point a model offers must quote the article's own words, and the quote is checked against the article
    text; a point whose quote is not there is dropped and counted. With no model, a fixed lexicon (`INTERESTS`) finds
    the points, each still quoting its sentence.

Whether it runs at all is a governance control, off for every firm until compliance turns it on with the lawful basis
in the firm's privacy-notice words (`rulebook.SETTINGS["persona_profiling"]` and `["persona_basis"]`), with a reason on
the audit log. Profiling with no basis on file does not run, and a refusal names compliance as the owner.

Articles come only from the news sources registered at `news_sources.py`; nothing here searches or scrapes the web.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import news_sources
from app.aurea_core.introductions import plain
from app.core import foundation
from app.models.crm import CrmContact
from app.models.graph import Person
from app.models.tenant import Firm

EXPIRY_DAYS = 180          # a talking point is good for six months from its article's date
MAX_POINTS = 12
MIN_QUOTE = 15

# The special categories, never a talking point. Matched as word starts, case aside.
EXCLUDED: dict[str, tuple[str, list[str]]] = {
    "health": ("Health", ["health", "illness", "cancer", "diagnos", "hospital", "surgery", "disease", "disabilit",
                          "disabled", "pregnan", "mental", "depression", "therapy", "medical", "recovering", "rehab",
                          "diabet", "stroke", "chemotherap", "clinic", "injur"]),
    "religion": ("Religion or belief", ["church", "mosque", "temple", "synagogue", "faith", "religio", "prayer", "pray",
                                        "christian", "muslim", "islam", "jewish", "hindu", "buddhis", "sikh", "catholic",
                                        "parish", "worship", "bible", "quran", "atheis", "marae"]),
    "politics": ("Political opinions", ["politic", "election", "candidate", "parliament", "member of parliament",
                                        "labour party", "national party", "green party", "act party", "party member",
                                        "campaign for", "voted", "voter", "conservative", "liberal", "republican",
                                        "democrat", "partisan", "donor to the"]),
    "sex_life": ("Sex life or sexual orientation", ["sexual", "sexuality", "gay", "lesbian", "bisexual", "transgender",
                                                    "lgbt", "queer", "dating"]),
    "ethnicity": ("Racial or ethnic origin", ["ethnic", "racial", "ancestry", "descent", "iwi", "heritage",
                                              "immigrant", "refugee", "nationality"]),
    "union": ("Trade union membership", ["trade union", "union member", "unionist", "collective bargaining", "shop steward"]),
    "criminal": ("Criminal matters", ["convict", "charged", "arrest", "prosecut", "crime", "criminal", "police",
                                      "sentenced", "offence", "offense", "guilty", "acquitted", "court case", "courtroom", "high court", "district court"]),
}
# Adverse media: the whole article goes to screening's side, never a talking point.
ADVERSE = ["fraud", "lawsuit", "sued", "suing", "sanction", "investigat", "fined", "fine of", "penalt", "misconduct",
           "bankrupt", "insolven", "liquidat", "receivership", "scandal", "laundering", "brib", "corrupt", "embezzl",
           "allegation", "alleged", "disqualified", "banned", "default on", "tax evasion", "whistleblow"]

# The deterministic fallback: an interest, at work or beyond, and the words that show it.
INTERESTS: list[tuple[str, str, list[str]]] = [
    ("Board and governance roles", "work", ["board", "chair", "chairs", "director", "trustee"]),
    ("Growing the business", "work", ["expan", "export", "new market", "acquisition", "acquired", "launch", "opened a", "opens a"]),
    ("Industry recognition", "work", ["award", "honoured", "honored", "recognised", "recognized", "finalist"]),
    ("Speaking and industry events", "work", ["conference", "keynote", "panel", "summit", "speaker", "spoke at"]),
    ("Sustainability", "work", ["sustainab", "renewable", "climate", "carbon", "solar", "regenerative"]),
    ("Mentoring", "work", ["mentor", "scholarship", "young entrepreneur", "apprentice"]),
    ("Technology", "work", ["technology", "software", "digital", "startup", "start-up", "automation"]),
    ("Sailing", "beyond", ["sail", "yacht", "regatta"]),
    ("Rugby", "beyond", ["rugby"]),
    ("Golf", "beyond", ["golf"]),
    ("Endurance sport", "beyond", ["marathon", "triathlon", "cycling", "cycle race", "ironman", "half-marathon"]),
    ("Wine", "beyond", ["wine", "vineyard", "winery", "pinot"]),
    ("The arts", "beyond", ["gallery", "exhibition", "painting", "sculpture", "art collection", "theatre"]),
    ("Music", "beyond", ["music", "orchestra", "concert", "jazz", "choir"]),
    ("Philanthropy", "beyond", ["charity", "charitable", "philanthrop", "fundrais", "donated", "pro bono"]),
    ("The outdoors", "beyond", ["tramping", "hiking", "fishing", "mountain", "outdoors", "conservation"]),
    ("Food", "beyond", ["cookbook", "restaurant", "chef", "food festival"]),
    ("Books and writing", "beyond", ["author", "memoir", "book club", "novel", "published a book"]),
]


class PersonaError(Exception):
    """Refused, with a reason fit to show the person."""


def _rx(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + ")", re.I)


_EXCLUDED_RX = {k: _rx(words) for k, (_label, words) in EXCLUDED.items()}
_ADVERSE_RX = _rx(ADVERSE)
_INTEREST_RX = [(label, domain, _rx(words)) for label, domain, words in INTERESTS]
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def excluded_in(text: str) -> list[str]:
    """The special categories a piece of text touches."""
    return [k for k, rx in _EXCLUDED_RX.items() if rx.search(text or "")]


def is_adverse(text: str) -> bool:
    return bool(_ADVERSE_RX.search(text or ""))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# ── The control ────────────────────────────────────────────────────────────────────────────────

def control(firm: Firm) -> dict:
    """Whether profiling runs at this firm and on what basis. Compliance's control, read from the firm's governance
    settings (`rulebook.SETTINGS`). On only when switched on with a basis on file."""
    pol = foundation.policy(firm)
    switched = bool(pol.get("persona_profiling"))
    basis = str(pol.get("persona_basis") or "").strip()
    marks = (getattr(firm, "settings", None) or {}).get("simulated_controls") or {}
    simulated = any(isinstance(m, dict) and m.get("persona_profiling") == pol.get("persona_profiling")
                    and m.get("persona_basis") == pol.get("persona_basis") for m in marks.values())
    return {"on": switched and bool(basis), "switched_on": switched, "basis": basis or None, "owner": "compliance",
            "simulated": bool(simulated and switched)}


def refusal(ctl: dict) -> str:
    if ctl["switched_on"] and not ctl["basis"]:
        return ("Profiling from public sources is switched on with no lawful basis on file, so it does not run. The basis "
                "is compliance's to record (agent control persona_basis, in the privacy notice's words).")
    return ("Profiling from public sources is off at this firm. Whether it runs, and its lawful basis, is compliance's "
            "to set (agent controls persona_profiling and persona_basis), with a reason on the audit log.")


# ── Who ───────────────────────────────────────────────────────────────────────────────────────

async def subject(session: AsyncSession, firm: Firm, *, contact_id=None, person_id=None, name: str | None = None) -> dict:
    def parse(v, label):
        try:
            return uuid.UUID(str(v))
        except ValueError:
            raise PersonaError(f"{label} {v!r} is not an id; read_crm_pipeline or search_households gives one.")

    if contact_id:
        c = await session.get(CrmContact, parse(contact_id, "contact_id"))
        if c is None or c.firm_id != firm.id:
            raise PersonaError("That contact is not one of this firm's contacts.")
        return {"type": "contact", "id": str(c.id), "name": c.full_name, "household_id": str(c.household_id) if c.household_id else None}
    if person_id:
        p = await session.get(Person, parse(person_id, "person_id"))
        if p is None or p.firm_id != firm.id:
            raise PersonaError("That person is not one of this firm's clients.")
        return {"type": "person", "id": str(p.id), "name": p.full_name, "household_id": str(p.household_id) if p.household_id else None}
    if name:
        want = plain(name)
        rows = [("contact", c) for c in (await session.execute(select(CrmContact).where(
            CrmContact.firm_id == firm.id, CrmContact.is_active.is_(True)))).scalars().all()]
        rows += [("person", p) for p in (await session.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()]
        hits = [(t, r) for t, r in rows if plain(r.full_name) == want] or [(t, r) for t, r in rows if want and want in plain(r.full_name)]
        # A client who is also a contact is one person: prefer the contact row for a prospect's brief.
        if len({plain(r.full_name) for _, r in hits}) == 1 and hits:
            t, r = sorted(hits, key=lambda h: h[0] != "contact")[0]
            return {"type": t, "id": str(r.id), "name": r.full_name, "household_id": str(r.household_id) if r.household_id else None}
        if not hits:
            raise PersonaError(f"No contact or client named {name!r}.")
        raise PersonaError(f"{len(hits)} match {name!r}: " + ", ".join(f"{r.full_name} ({t})" for t, r in hits[:6])
                           + ". Give contact_id or person_id.")
    raise PersonaError("Say who: contact_id, person_id or name.")


# ── The persona ───────────────────────────────────────────────────────────────────────────────

def _filtered(article: news_sources.Article, left: dict) -> str | None:
    """The article's words with every special-category sentence taken out, or None if it is adverse media."""
    if is_adverse(f"{article.title}. {article.text}"):
        left["adverse_media"] += 1
        return None
    kept = []
    for sentence in _SENTENCE.split(_squash(article.text)):
        cats = excluded_in(sentence)
        if cats:
            left["special_category"] += 1
            for c in cats:
                left["by_category"][c] = left["by_category"].get(c, 0) + 1
            continue
        kept.append(sentence)
    return " ".join(kept)


def _rule_points(texts: list[tuple[news_sources.Article, str]]) -> list[dict]:
    """The fallback: the first sentence in each article that shows an interest in the lexicon, quoted."""
    out = []
    for article, text in texts:
        seen = set()
        for sentence in _SENTENCE.split(text):
            for label, domain, rx in _INTEREST_RX:
                if label not in seen and rx.search(sentence):
                    seen.add(label)
                    out.append({"point": label, "domain": domain, "quote": sentence.strip(), "_article": article})
    return out


async def _model_points(session: AsyncSession, firm: Firm, who: str, texts: list[tuple[news_sources.Article, str]]) -> list[dict] | None:
    """Points a model offers, from the filtered texts only, each with the exact words it rests on. None with no model."""
    from app.llm import usage as usage_mod
    from app.llm.service import firm_llm_creds, llm_service

    if not texts or not llm_service.enabled(firm_llm_creds(firm)):
        return None
    gp = await usage_mod.gateway_params(session, firm, base_max_tokens=900)
    numbered = "\n\n".join(f"[{i}] {a.title} ({a.publisher}, {a.published_on.isoformat()})\n{t}" for i, (a, t) in enumerate(texts))
    system = ("You list conversation talking points about a person from articles. Use only the articles' words. For each "
              "point give the article number, a short label (a few words), the domain (work or beyond), and the exact "
              "words from the article it rests on, copied verbatim. Nothing about health, religion, politics, sex life, "
              "ethnicity, union membership, crime, or anything adverse. Answer with a JSON array only: "
              '[{"article": 0, "point": "...", "domain": "work", "quote": "..."}].')
    result = await llm_service.generate(task="classify", system=system, prompt=f"Person: {who}\n\nArticles:\n{numbered}",
                                        firm_model_config=(firm.model_config_json or {}), creds=firm_llm_creds(firm),
                                        max_tokens=gp["max_tokens"], temperature=0.0, fallback=lambda: "",
                                        pii_terms=gp["pii_terms"], pii_categories=gp["pii_categories"], redact=gp["redact"],
                                        allow_fallback=gp["allow_fallback"], force_fallback=gp["force_fallback"])
    try:
        await usage_mod.record(session, firm.id, agent="persona", task="classify", result=result)
    except Exception:   # telemetry never breaks a read
        pass
    if result.is_fallback or not result.text.strip():
        return None
    raw = result.text[result.text.find("["): result.text.rfind("]") + 1]
    try:
        items = json.loads(raw)
    except ValueError:
        return None
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            article, text = texts[int(it.get("article"))]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        out.append({"point": str(it.get("point") or "").strip(), "domain": str(it.get("domain") or "").strip().lower(),
                    "quote": str(it.get("quote") or "").strip(), "_article": article, "_text": text})
    return out


def checked(point: dict, text: str) -> bool:
    """A point stands only on the words it quotes: the quote is in the (filtered) article, long enough to mean
    something, and neither the point nor its quote touches a special category or adverse media."""
    quote, label = _squash(point.get("quote")), _squash(point.get("point"))
    if len(quote) < MIN_QUOTE or not label or len(label) > 80 or point.get("domain") not in ("work", "beyond"):
        return False
    if quote.lower() not in _squash(text).lower():
        return False
    return not excluded_in(f"{label}. {quote}") and not is_adverse(f"{label}. {quote}")


async def build(session: AsyncSession, firm: Firm, *, contact_id=None, person_id=None, name: str | None = None,
                use_model: bool = True) -> dict:
    """The persona, or why there is none. Raises PersonaError only for who it is about."""
    from app.aurea_core import firm_time

    who = await subject(session, firm, contact_id=contact_id, person_id=person_id, name=name)
    ctl = control(firm)
    base = {"subject": who, "control": ctl, "allowed": ctl["on"], "expiry_days": EXPIRY_DAYS,
            "excluded_categories": [label for label, _ in EXCLUDED.values()]}
    if not ctl["on"]:
        return {**base, "refusal": refusal(ctl), "points": [], "sources": [], "simulated": False}
    today = firm_time.today(firm)
    sources = news_sources.for_firm(firm)
    left = {"special_category": 0, "by_category": {}, "adverse_media": 0, "unchecked": 0, "expired": 0}
    articles: list[news_sources.Article] = []
    for src in sources:
        for a in await src.articles(firm, name=who["name"]):
            a.source = a.source or src.key
            a.simulated = bool(a.simulated or getattr(src, "simulated", False))
            articles.append(a)
    fresh = []
    for a in sorted(articles, key=lambda a: a.published_on, reverse=True):
        if a.published_on > today:
            continue
        if a.published_on + timedelta(days=EXPIRY_DAYS) < today:
            left["expired"] += 1
            continue
        fresh.append(a)
    texts = [(a, t) for a in fresh if (t := _filtered(a, left))]
    offered = await _model_points(session, firm, who["name"], texts) if use_model else None
    method = "model" if offered is not None else "rules"
    if offered is None:
        offered = _rule_points(texts)
    text_of = {id(a): t for a, t in texts}
    points, labels = [], set()
    for p in offered:
        a = p["_article"]
        if not checked(p, text_of.get(id(a), "")):
            left["unchecked"] += 1
            continue
        if p["point"].lower() in labels:
            continue        # one point per interest: the newest article's
        labels.add(p["point"].lower())
        points.append({"point": p["point"], "domain": p["domain"], "quote": _squash(p["quote"]),
                       "article": a.ref(), "dated": a.published_on.isoformat(),
                       "expires_on": (a.published_on + timedelta(days=EXPIRY_DAYS)).isoformat(), "simulated": a.simulated})
    points = points[:MAX_POINTS]
    not_on_file = [] if sources else ["A licensed news source"]
    return {**base, "points": points, "left_out": left, "articles_read": len(articles), "extraction": method,
            "sources": [news_sources.describe(s) for s in sources], "not_on_file": not_on_file,
            "simulated": any(p["simulated"] for p in points) or any(getattr(s, "simulated", False) for s in sources),
            "as_of": today.isoformat()}


async def read(session: AsyncSession, firm: Firm, **who) -> dict:
    out = await build(session, firm, **who)
    if not out["allowed"]:
        raise PersonaError(out["refusal"])
    return out
