"""Invented articles, from invented publishers, about the demonstration firm's invented prospects and clients only.

Nothing here is about a real person and no publisher here exists: every title carries "(simulated)" in its
publisher, every link is on the reserved `.example` domain. Dates are days before the firm's today, so the demo does
not age. A few are written to show what the persona leaves out: an article older than the expiry, a sentence on a
special category (health), and an adverse article, which stays with screening.
"""
from __future__ import annotations

# name -> [(days ago, publisher, title, slug, text)]
PUBLISHERS = ("Harbourside Business Journal (simulated)", "The Tasman Ledger (simulated)",
              "Southern Regatta Notes (simulated)", "Ridgeline Arts Weekly (simulated)")
HBJ, TL, SRN, RAW = PUBLISHERS

ARTICLES: dict[str, list[tuple[int, str, str, str, str]]] = {
    "James Whitaker": [
        (21, HBJ, "Whitaker Logistics opens a cold-chain hub in Tauranga", "whitaker-cold-chain",
         "James Whitaker said the new hub would double the firm's export capacity to Asia within two years. "
         "The company is expanding into refrigerated freight after a decade in dry goods. "
         "Whitaker chairs the regional chamber of commerce's export committee."),
        (64, SRN, "Classic yacht Kōtuku takes line honours", "kotuku-line-honours",
         "Skippered by James Whitaker, the restored 1938 yacht Kōtuku took line honours in the harbour classic regatta. "
         "Whitaker, who was back racing after knee surgery in the winter, credited a young crew. "
         "He has sailed the race every year since 2009."),
        (110, TL, "Freight operator named in supplier dispute", "freight-supplier-dispute",
         "A lawsuit filed by a former supplier names Whitaker Logistics over unpaid invoices. "
         "The company said it would defend the claim."),
        (420, HBJ, "Whitaker Logistics wins regional exporter award", "whitaker-award-old",
         "James Whitaker accepted the regional exporter award on behalf of the company."),
    ],
    "Grace Liu": [
        (12, HBJ, "Liu Architecture to design waterfront library", "liu-library",
         "Grace Liu's practice won the commission for the new waterfront library, its largest public project. "
         "Liu spoke at the national design summit last month on timber construction and sustainable building."),
        (90, RAW, "Studio collection goes on show", "liu-collection",
         "Architect Grace Liu lent six works from her collection of contemporary ceramics to the gallery's summer exhibition. "
         "She sits on the gallery's board of trustees."),
    ],
    "Hemi Walker": [
        (30, TL, "Walker Orchards moves into kiwifruit exports", "walker-kiwifruit",
         "Hemi Walker said the orchard business would launch its first direct export programme next season. "
         "Walker mentors young growers through the regional horticulture scholarship."),
        (75, SRN, "Masters rugby side tours the Pacific", "masters-rugby",
         "Hemi Walker, captain of the masters rugby side, led the team on a three-match tour."),
    ],
    "Wei Chen": [
        (40, HBJ, "Chen Imports marks thirty years", "chen-thirty",
         "Wei Chen founded the business in 1995 and remains its chair. "
         "The company recently opened a second warehouse in the south."),
        (140, RAW, "Vineyard tasting raises funds for the rescue helicopter", "chen-vineyard",
         "Wei Chen hosted a charity tasting at his Central Otago vineyard, where he grows pinot noir. "
         "The evening raised funds for the regional rescue helicopter."),
    ],
    "Olivia Harris": [
        (25, HBJ, "Harris Legal opens Wellington office", "harris-legal",
         "Olivia Harris said the firm would expand its commercial property team. "
         "Harris was a finalist for the young lawyer of the year award."),
        (55, SRN, "Harbour marathon results", "harbour-marathon",
         "Olivia Harris finished the harbour marathon in under four hours, her third this year."),
    ],
    "Rohan Mehta": [
        (35, HBJ, "Software start-up raises seed round", "mehta-seed",
         "Rohan Mehta's software startup closed its seed round to build automation tools for small manufacturers. "
         "Mehta is a keynote speaker at the regional technology conference in March."),
    ],
    "Binh Nguyen": [
        (50, TL, "Nguyen Engineering joins the infrastructure board", "nguyen-board",
         "Binh Nguyen was appointed to the board of the regional infrastructure trust. "
         "He is a keen golfer and plays off a handicap of eight."),
    ],
}


def url(slug: str) -> str:
    return f"https://simulated-news.example/{slug}"


# India (B-256): invented publishers, about the Indian demonstration firm's invented clients only. Days are before the firm's today.
DBC, KMD, MSW = ("Deccan Business Courier (simulated)", "Konkan Markets Daily (simulated)", "Malabar Sports Weekly (simulated)")
ARTICLES_IN: dict[str, list[tuple[int, str, str, str, str]]] = {
    "Rajesh Sharma": [
        (18, DBC, "Sharma Precision Tools opens a second plant near Pune", "sharma-second-plant",
         "Rajesh Sharma said the new plant would add capacity for components sold to two-wheeler makers. "
         "Sharma chairs the regional manufacturers' association's skills committee."),
        (70, MSW, "Veterans' cricket league crowns its champions", "sharma-veterans-cricket",
         "Rajesh Sharma captained the winning side in the veterans' league final. "
         "He has played the league every season since it began."),
        (300, DBC, "Sharma Precision Tools wins a state export award", "sharma-export-award",
         "Rajesh Sharma accepted the state export award on behalf of the company."),
    ],
    "Karthik Iyer": [
        (26, KMD, "Iyer Analytics raises a growth round", "iyer-growth-round",
         "Karthik Iyer's analytics firm closed a growth round to expand its work with regional banks. "
         "Iyer is a visiting lecturer at a management institute."),
        (95, MSW, "Chess association names new patron", "iyer-chess-patron",
         "Karthik Iyer, a lifelong chess player, was named patron of the state junior chess association. "
         "He recently recovered from a long illness and thanked the doctors who treated him."),
    ],
    "Nandini Kapoor": [
        (33, KMD, "Kapoor Family Trust funds a school library", "kapoor-school-library",
         "Nandini Kapoor said the trust would fund a library and a reading programme at a municipal school. "
         "She has supported the school for fifteen years."),
    ],
}
