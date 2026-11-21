"""Where articles about a named person or company come from (B-202): the seam a licensed news and company-data
provider plugs into.

A source is registered once, at start-up (`register`), and says for which firms it applies: a licensed provider for
the firms that hold the licence, a simulation for the demonstration firm that opted in to it. `persona.py` reads every
source that applies to a firm and nothing else; it never scrapes and never searches the open web. A source returns
`Article`s: what was published, by whom, when, where, and its words (the text or a summary), so every talking point
can quote the words it rests on. A source that is not a real service says so (`simulated = True`), and so does every
article it returns.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Protocol


@dataclass
class Article:
    title: str
    publisher: str
    published_on: date
    url: str
    text: str                    # the article's text, or the provider's summary of it
    source: str = ""             # the registered source's key
    simulated: bool = False

    def ref(self) -> dict:
        d = asdict(self)
        d["published_on"] = self.published_on.isoformat()
        d.pop("text", None)
        return d


class NewsSource(Protocol):
    key: str
    name: str
    simulated: bool

    def applies_to(self, firm) -> bool:
        """Whether this firm may read this source (holds its licence, or opted in to a simulation)."""

    async def articles(self, firm, *, name: str, company: str | None = None) -> list[Article]:
        """Articles about the named person (or their company), newest first."""


SOURCES: dict[str, NewsSource] = {}


def register(source: NewsSource) -> None:
    SOURCES[source.key] = source


def unregister(key: str) -> None:
    SOURCES.pop(key, None)


def for_firm(firm) -> list[NewsSource]:
    out = []
    for s in SOURCES.values():
        try:
            if s.applies_to(firm):
                out.append(s)
        except Exception:   # a source that cannot say is not read
            continue
    return out


def describe(source: NewsSource) -> dict:
    return {"key": source.key, "name": source.name, "simulated": bool(getattr(source, "simulated", False))}
