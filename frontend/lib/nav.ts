import { firmMarket } from "./format";
// The one list of pages ("Views") in the product, shared by the classic shell's sidebar, the
// Astra workspace rail and the command palette so they cannot disagree about who sees what.
//
// Pages are artifact views the agents open; they are not the navigation model. The primary way
// in is the conversation (/workspace). A role's handful of everyday views are PINNED; everything
// else it may open lives under "More views", grouped by intent, and is reachable by typing in
// the palette. Routes here are stable: never rename or remove one.
import {
  LayoutDashboard, Users, ClipboardCheck, ShieldCheck, Sparkles, Settings,
  PlugZap, UserPlus, GitMerge, CalendarClock, FileText, CheckSquare, MessageSquare, Gauge, Activity,
  BarChart3, Workflow, PiggyBank, Scale, Wand2, History, UserCircle, Landmark, Target, Receipt,
  Brain, Timer, TrendingUp, Repeat, UserCheck, LineChart, Banknote, Building2, ArrowLeftRight, Umbrella,
  SlidersHorizontal, BadgeCheck, CalendarCheck, ScrollText, Wallet, DoorOpen, PackageCheck, Gem, Compass, FolderOpen,
  Sprout, ClipboardList, Lightbulb,
} from "lucide-react";

export type ViewIntent = "decide" | "clients" | "operate" | "govern" | "configure" | "platform";

export interface View {
  href: string;
  label: string;
  icon: any;
  /** Roles that see it. Omitted = every staff role. [] = admin only. "superadmin_only" = platform owner. */
  roles?: string[];
  intent: ViewIntent;
  /** Extra words the palette matches on, so "invoice" finds Billing and "kyc" finds Onboarding. */
  keywords?: string[];
  /** Only for firms in this market. */
  market?: string;
  /** Not for firms in these markets: the page's rules are another market's and say "none here" for them. */
  hiddenIn?: string[];
  /** The name a market's own firms know the page by. */
  labelIn?: Record<string, string>;
  /** A section's menu entry: the pages it stands for (SECTIONS). */
  members?: string[];
}

export const INTENT_TITLES: Record<ViewIntent, string> = {
  decide: "Decide",
  clients: "Clients & advice",
  operate: "Run the business",
  govern: "Oversight",
  configure: "Configure",
  platform: "Platform",
};

export const INTENT_ORDER: ViewIntent[] = ["decide", "clients", "operate", "govern", "configure", "platform"];

const A = "adviser", P = "paraplanner", PT = "portfolio_team", R = "research_cio",
  C = "compliance", O = "operations", B = "branch_leader";

export const VIEWS: View[] = [
  // Decide
  { href: "/studio", label: "Cockpit", icon: LayoutDashboard, roles: [A, P, PT, B], intent: "decide", keywords: ["home", "feed", "delegate"] },
  { href: "/studio/review", label: "Recommendations", icon: ClipboardCheck, roles: [A, PT, C], intent: "decide", keywords: ["approve", "proposals", "review", "decisions"] },
  { href: "/studio/tasks", label: "Tasks", icon: CheckSquare, roles: [A, P, C], intent: "decide", keywords: ["to do", "todo"] },

  // Clients & advice
  { href: "/studio/clients", label: "Clients", icon: Users, roles: [A, P, PT, R, O, B], intent: "clients", keywords: ["households", "families"] },
  { href: "/studio/meetings", label: "Meetings", icon: CalendarClock, roles: [A, P], intent: "clients", keywords: ["prep", "brief", "calendar"] },
  { href: "/studio/reports", label: "Reports", icon: FileText, roles: [A, P, R], intent: "clients", keywords: ["research", "packs"] },
  { href: "/studio/messages", label: "Messages", icon: MessageSquare, roles: [A, P], intent: "clients", keywords: ["client care", "outreach"] },
  { href: "/studio/family", label: "Family aggregate", icon: UserCircle, roles: [A, P, PT], intent: "clients", keywords: ["household wealth", "aggregate"] },
  { href: "/studio/estate", label: "Estate & succession", icon: Landmark, roles: [A, P, PT], intent: "clients", keywords: ["wills", "heirs", "trust"] },
  { href: "/studio/tax", label: "Tax intelligence", icon: Receipt, roles: [A, P, PT], intent: "clients", keywords: ["harvest", "wash sale", "capital gains"] },
  { href: "/studio/licences", label: "Licences & SEBI", icon: BadgeCheck, roles: [A, P, PT, C, O, B], intent: "clients", market: "IN", keywords: ["sebi", "consent", "investment adviser", "portfolio manager", "pms", "client consent", "before advice", "fee cap", "compliance calendar", "filings", "iaasb"] },
  { href: "/studio/protection", label: "Protection", icon: Umbrella, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["insurance", "life cover", "disability", "income protection", "tpd", "gap"] },
  { href: "/studio/risk-profiles", label: "Risk profiles", icon: SlidersHorizontal, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["questionnaire", "tolerance", "capacity", "suitability", "risk score"] },
  { href: "/studio/reviews", label: "Client reviews", icon: CalendarCheck, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["annual review", "cadence", "next review", "overdue", "reverse churning", "ongoing service"] },
  { href: "/studio/ips", label: "IPS monitoring", icon: ScrollText, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["investment policy", "statement", "breach", "drift", "exception", "limits", "restricted"] },
  { href: "/studio/net-worth", label: "Net worth", icon: Wallet, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["balance sheet", "liabilities", "debts", "mortgage", "held away", "expenses", "cash flow", "assets"] },
  { href: "/studio/products", label: "Product shelf", icon: PackageCheck, roles: [A, P, PT, C, O, B, R], intent: "clients", keywords: ["approved list", "due diligence", "funds", "share class", "accredited", "closed"] },
  { href: "/studio/equity-comp", label: "Equity compensation", icon: Gem, roles: [A, P, PT, C, O, B], intent: "clients", hiddenIn: ["IN"], keywords: ["rsu", "iso", "espp", "stock options", "concentrated stock", "vesting", "10b5-1", "collar"] },
  { href: "/studio/plan", label: "Financial plan", icon: Compass, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["retirement", "social security", "pension", "withdrawal", "emergency fund", "debt", "education", "529", "monte carlo"] },
  { href: "/studio/client-file", label: "Client file", icon: FolderOpen, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["trusted contact", "will", "power of attorney", "health directive", "life event", "marriage", "divorce"] },
  { href: "/studio/advice-file", label: "Advice file", icon: Lightbulb, roles: [A, P, PT, C, B], intent: "clients", keywords: ["recommendation", "advice given", "declined", "accepted", "implementation rate", "alternatives", "suitability record"] },
  { href: "/studio/plans", label: "Plans of record", icon: ClipboardCheck, roles: [A, P, PT, C, B], intent: "clients", keywords: ["plan of record", "baseline", "plan review", "peer review", "qc", "delivered plan", "coverage", "freshness", "scenario depth"] },
  { href: "/studio/cash", label: "Cash movements", icon: Banknote, roles: [A, P, PT, C, O, B], intent: "operate", keywords: ["withdrawal", "wire", "standing instruction", "sloa", "rmd", "distribution", "phasing", "dollar cost"] },
  { href: "/studio/trades", label: "Trades", icon: ArrowLeftRight, roles: [A, P, PT, C, O, B], intent: "operate", keywords: ["blotter", "confirmation", "settlement", "block", "allocation", "orders"] },
  { href: "/studio/tax-documents", label: "Tax documents", icon: FileText, roles: [A, P, PT, C, O, B], intent: "operate", keywords: ["1099", "k-1", "tax forms", "tax summary"] },
  { href: "/studio/onboarding-queues", label: "Onboarding queues", icon: Workflow, roles: [A, P, C, O, B], intent: "operate", keywords: ["exceptions", "nigo", "clocks", "sla", "account opening", "funding"] },
  { href: "/studio/repapering", label: "Repapering", icon: ScrollText, roles: [A, P, C, O, B], intent: "govern", hiddenIn: ["IN"], keywords: ["repapering", "negative consent", "affirmative consent", "conversion", "recruitment", "bulk"] },
  { href: "/studio/onboarding-scoreboard", label: "Onboarding scoreboard", icon: Gauge, roles: [A, P, C, O, B], intent: "govern", keywords: ["days to invest", "abandonment", "nps", "raci", "fte", "scoreboard", "onboarding metrics"] },
  { href: "/studio/account-features", label: "Account features", icon: SlidersHorizontal, roles: [A, P, C, O, B], intent: "govern", hiddenIn: ["IN"], keywords: ["margin", "options", "checkwriting", "senior", "2165", "exploitation", "hold", "trusted contact"] },
  { href: "/studio/money-in", label: "Money in", icon: Wallet, roles: [A, P, C, O, B], intent: "govern", keywords: ["ach", "deposit", "bank link", "micro-deposit", "return", "hold", "standing instruction", "revalidate", "upi", "neft", "rtgs", "netbanking", "mandate", "registered bank", "cooling-off", "payer", "nre", "nro", "remittance"] },
  { href: "/studio/monitoring", label: "Transaction monitoring", icon: ShieldCheck, roles: [C, O], intent: "govern", keywords: ["aml", "monitoring", "sar", "str", "suspicious", "investigation", "structuring", "alerts"] },
  { href: "/studio/transfers", label: "Transfers in", icon: ArrowLeftRight, roles: [A, P, C, O, B], intent: "govern", hiddenIn: ["IN"], keywords: ["acat", "transfer", "reject", "cost basis", "rollover", "1035", "asset memo", "residual"] },
  { href: "/studio/consolidation", label: "Consolidation", icon: GitMerge, roles: [A, P, PT, C, O, B], intent: "operate", market: "IN", keywords: ["folio", "fund house", "code change", "arn", "off-market transfer", "demat", "pms transition", "gain budget", "redeem", "cas"] },
  { href: "/studio/document-matrix", label: "Document matrix", icon: ScrollText, roles: [A, P, C, O, B], intent: "govern", keywords: ["nigo", "documents", "registration", "tenancy", "custodian forms", "signatures", "beneficiaries"] },
  { href: "/studio/consents", label: "Consents", icon: BadgeCheck, roles: [A, P, C, O, B], intent: "govern", keywords: ["consent", "e-delivery", "householding", "aggregation", "fee debit", "redelivery", "brochure"] },
  { href: "/studio/aml", label: "AML reviews", icon: ShieldCheck, roles: [A, C, O, B], intent: "govern", keywords: ["screening", "sanctions", "pep", "edd", "source of wealth", "kyc", "hits"] },
  { href: "/studio/exits", label: "Departures and estates", icon: DoorOpen, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["offboarding", "leaving", "acat", "transfer out", "death", "estate settlement", "executor", "step-up", "inherited"] },
  { href: "/studio/behavioural", label: "Behavioural finance", icon: Brain, roles: [A, P, PT], intent: "clients", keywords: ["bias", "nudge"] },
  { href: "/studio/beneficiary-audit", label: "Beneficiary audit", icon: UserCheck, roles: [A, P, PT, C], intent: "clients", labelIn: { IN: "Nominations" }, keywords: ["registration", "beneficiaries", "nominees", "nomination"] },
  { href: "/studio/pipeline", label: "Growth pipeline", icon: TrendingUp, roles: [A, P, PT, B], intent: "clients", keywords: ["crm", "prospects", "opportunities"] },
  { href: "/canvas", label: "Client portal", icon: Sparkles, roles: [A, P, B], intent: "clients", keywords: ["client experience", "wealth"] },
  { href: "/canvas/retirement", label: "Client view — Retirement", icon: PiggyBank, roles: [A, P, B], intent: "clients", keywords: ["planner"] },
  { href: "/canvas/goals", label: "Client view — Goals", icon: Target, roles: [A, P, B], intent: "clients" },
  { href: "/canvas/next-gen", label: "Client view — Next-gen", icon: Sprout, roles: [A, P, B], intent: "clients", keywords: ["heir", "next generation", "journey"] },
  { href: "/canvas/questionnaire", label: "Client view — Questionnaire", icon: ClipboardList, roles: [A, P], intent: "clients", keywords: ["risk questionnaire", "answers", "tolerance"] },

  // Run the business
  { href: "/studio/onboarding", label: "Onboarding", icon: UserPlus, roles: [O, C, A], intent: "operate", keywords: ["kyc", "aml", "cases", "new client"] },
  { href: "/studio/book-integration", label: "Book integration", icon: GitMerge, roles: [O], intent: "operate", keywords: ["import", "acquire", "book scan"] },
  { href: "/studio/billing", label: "Billing", icon: Banknote, roles: [A, C, O, B], intent: "operate", keywords: ["fees", "invoices", "run", "revenue"] },
  { href: "/studio/prices", label: "Prices", icon: Activity, roles: [A, P, PT, R, C, O, B], intent: "operate", keywords: ["freshness", "stale", "quote", "feed", "market data", "refresh"] },
  { href: "/studio/reconciliation", label: "Reconciliation", icon: ArrowLeftRight, roles: [PT, O, C, B], intent: "operate", keywords: ["custodian", "statement", "breaks", "positions", "cash", "match"] },
  { href: "/studio/valuations", label: "Valuations", icon: Landmark, roles: [PT, O, C, B], intent: "operate", keywords: ["private", "nav", "statement", "fund", "stated value"] },
  { href: "/studio/corporate-actions", label: "Corporate actions", icon: Repeat, roles: [O, C], intent: "operate", keywords: ["splits", "dividends", "entitlements"] },
  { href: "/studio/composites", label: "Composites", icon: LineChart, roles: [A, PT, R, C], intent: "operate", keywords: ["gips", "performance"] },
  { href: "/studio/analytics", label: "Analytics", icon: BarChart3, roles: [A, PT, R, B], intent: "operate", keywords: ["book", "allocation"] },
  { href: "/studio/capacity", label: "Capacity & outcomes", icon: Gauge, roles: [B, A], intent: "operate", keywords: ["hours", "workload"] },

  // Govern the agents
  { href: "/provenance", label: "Provenance", icon: ShieldCheck, roles: [C, B], intent: "govern", keywords: ["ledger", "audit", "decision record"] },
  { href: "/studio/regulatory-countdown", label: "Regulatory countdown", icon: Timer, roles: [A, P, PT], intent: "govern", keywords: ["deadlines", "rules", "advance tax", "itr", "kyc review"] },
  { href: "/studio/agent-history", label: "Agent history", icon: History, roles: [A, PT, C, B], intent: "govern", keywords: ["runs", "activity"] },
  { href: "/studio/workforce", label: "Workforce", icon: Workflow, roles: [A, PT, C, B], intent: "govern", keywords: ["agents", "autonomy", "tier", "kill switch"] },
  { href: "/studio/skills", label: "Skills", icon: Wand2, roles: [A, P, PT], intent: "govern", keywords: ["playbooks"] },

  // Configure
  { href: "/admin", label: "Firm & agents", icon: Settings, roles: [], intent: "configure", keywords: ["users", "branding", "settings", "models", "schedules"] },
  { href: "/admin/foundation", label: "Agent controls", icon: ShieldCheck, roles: [], intent: "configure" },
  { href: "/admin/regulatory", label: "Regulatory", icon: Scale, roles: [C], intent: "configure", keywords: ["frameworks", "rules"] },
  { href: "/admin/connectors", label: "Connectors", icon: PlugZap, roles: [O], intent: "configure", keywords: ["integrations", "custodian"] },

  // Platform
  { href: "/superadmin", label: "Firm management", icon: Building2, roles: ["superadmin_only"], intent: "platform" },
];

// ── Consolidation (usability review, 22 Nov). Two kinds of page left the menu:
//
//   * A page about one client, which shows only a list of households without one, is a section of that client:
//     it is reached from the client (ClientSections) and the palette, never listed on its own.
//   * Pages that belong together are one entry, a SECTION: the menu lists the section once, and every member
//     shows the same tabs (SectionTabs), so moving between them is one click and the menu stays short.
//
// Nothing is removed: every route still opens, by URL, from a card, from Astra and from the palette.

/** Pages about one client: listed under the client, not in the menu. */
export const CLIENT_ONLY = new Set([
  "/studio/plan", "/studio/estate", "/studio/family", "/studio/behavioural", "/studio/regulatory-countdown",
  "/canvas", "/canvas/retirement", "/canvas/goals", "/canvas/next-gen", "/canvas/questionnaire",
]);

/** Views that read one household when given `?household=`; a client's tabs carry it between them. */
export const TAKES_HOUSEHOLD = new Set([
  ...CLIENT_ONLY, "/studio/ips", "/studio/reviews", "/studio/protection", "/studio/risk-profiles", "/studio/net-worth",
  "/studio/equity-comp", "/studio/client-file", "/studio/advice-file", "/studio/cash", "/studio/trades", "/studio/tax-documents",
  "/studio/consents", "/studio/exits", "/studio/tax", "/studio/consolidation",
]);

export interface Section {
  key: string;
  label: string;
  icon: any;
  intent: ViewIntent;
  /** In tab order; the first a role may see is where the menu entry lands. */
  members: string[];
}

export const SECTIONS: Section[] = [
  { key: "advice", label: "Advice checks", icon: CalendarCheck, intent: "clients", members: [
    "/studio/reviews", "/studio/ips", "/studio/risk-profiles", "/studio/protection", "/studio/net-worth", "/studio/tax",
    "/studio/equity-comp", "/studio/client-file", "/studio/advice-file", "/studio/plans", "/studio/exits", "/studio/beneficiary-audit"] },
  { key: "onboarding", label: "Onboarding", icon: UserPlus, intent: "operate", members: [
    "/studio/onboarding", "/studio/onboarding-queues", "/studio/consolidation", "/studio/document-matrix", "/studio/consents", "/studio/transfers",
    "/studio/money-in", "/studio/account-features", "/studio/aml", "/studio/onboarding-scoreboard"] },
  { key: "money", label: "Money & trades", icon: ArrowLeftRight, intent: "operate", members: [
    "/studio/trades", "/studio/cash", "/studio/tax-documents", "/studio/reconciliation", "/studio/valuations", "/studio/prices",
    "/studio/corporate-actions", "/studio/book-integration"] },
  { key: "insights", label: "Insights", icon: BarChart3, intent: "operate", members: [
    "/studio/analytics", "/studio/composites", "/studio/capacity"] },
  { key: "compliance", label: "Compliance", icon: ShieldCheck, intent: "govern", members: [
    "/provenance", "/studio/monitoring", "/studio/repapering", "/admin/regulatory"] },
  { key: "agents", label: "Agents", icon: Workflow, intent: "govern", members: [
    "/studio/workforce", "/studio/agent-history", "/studio/skills"] },
  { key: "settings", label: "Settings", icon: Settings, intent: "configure", members: [
    "/admin", "/admin/foundation", "/admin/connectors"] },
];

const SECTION_OF: Record<string, Section> = Object.fromEntries(SECTIONS.flatMap((s) => s.members.map((m) => [m, s])));

/** The section a route belongs to (its path, any query ignored). */
export function sectionOf(route: string): Section | undefined {
  return SECTION_OF[route.split("?")[0]];
}

/** The members of a section this role may open, in tab order. */
export function sectionMembers(section: Section, role: string): View[] {
  const mine = viewsForRole(role);
  return section.members.map((h) => mine.find((v) => v.href === h)).filter((v): v is View => !!v);
}

/** What a client's page links to: its sections, in the order an adviser reaches for them. */
// Pairs, not objects with an href key: tests and the page sweep read every quoted href in this file as a view,
// and these are not views.
const CLIENT_SECTION_PAIRS: [string, string][] = [
  ["/studio/plan", "Plan"],
  ["/canvas/goals", "Goals"],
  ["/canvas/retirement", "Retirement"],
  ["/studio/net-worth", "Net worth"],
  ["/studio/consolidation", "Consolidation"],
  ["/studio/protection", "Protection"],
  ["/studio/risk-profiles", "Risk profile"],
  ["/studio/ips", "IPS"],
  ["/studio/reviews", "Reviews"],
  ["/studio/tax", "Tax"],
  ["/studio/estate", "Estate"],
  ["/studio/family", "Family"],
  ["/studio/equity-comp", "Equity comp"],
  ["/studio/client-file", "Client file"],
  ["/studio/advice-file", "Advice file"],
  ["/studio/cash", "Cash"],
  ["/studio/trades", "Trades"],
  ["/studio/behavioural", "Behavioural"],
  ["/studio/regulatory-countdown", "Deadlines"],
  ["/canvas/questionnaire", "Questionnaire"],
];
export const CLIENT_SECTIONS: { href: string; label: string }[] = CLIENT_SECTION_PAIRS.map(([href, label]) => ({ href, label }));

/** The handful of views each role opens every day. Everything else is under "More views". */
// A pin is a page, or "section:<key>" for a whole section (its tabs reach the rest). Two pages of one section are
// never both pinned: the section is pinned once instead.
export const ROLE_PINNED: Record<string, string[]> = {
  adviser: ["/studio/review", "/studio/clients", "/studio/meetings", "/studio/tasks"],
  paraplanner: ["/studio/meetings", "/studio/clients", "/studio/reports", "/studio/tasks"],
  portfolio_team: ["/studio/review", "/studio/clients", "section:insights", "section:money"],
  research_cio: ["/studio/reports", "/studio/products", "section:insights", "/studio/clients"],
  compliance: ["/studio/review", "section:compliance", "section:onboarding", "section:agents"],
  operations: ["section:onboarding", "section:money", "/studio/billing", "/studio/clients"],
  branch_leader: ["/studio/clients", "section:insights", "/studio/billing", "section:compliance"],
  admin: ["section:settings", "section:agents", "section:compliance"],
};

/** In India the licence decides what may be done for a client, so its page is pinned for the roles that work there,
 * second, after the role's first page. Elsewhere it does not exist. */
const MARKET_PINS: Record<string, Record<string, string>> = {
  IN: { adviser: "/studio/licences", paraplanner: "/studio/licences", portfolio_team: "/studio/licences",
        compliance: "/studio/licences", branch_leader: "/studio/licences", operations: "/studio/licences" },
};

function pinsFor(role: string): string[] | undefined {
  const pins = ROLE_PINNED[role];
  const extra = MARKET_PINS[firmMarket()]?.[role];
  if (!pins || !extra || pins.includes(extra)) return pins;
  return [pins[0], extra, ...pins.slice(1)];
}

/** A section as one menu entry for this role: named for the section when the role sees two or more of its pages,
 * the one page itself when it sees one, nothing when it sees none. */
function sectionEntry(section: Section, role: string): View | null {
  const members = sectionMembers(section, role);
  if (members.length === 0) return null;
  if (members.length === 1) return members[0];
  return { href: members[0].href, label: section.label, icon: section.icon, intent: section.intent, members: members.map((m) => m.href) };
}

/** Every view this role may open. Mirrors the old sidebar's rule exactly. */
export function viewsForRole(role: string): View[] {
  const market = firmMarket();
  const inMarket = VIEWS.filter((v) => (!v.market || v.market === market) && !v.hiddenIn?.includes(market))
    .map((v) => (v.labelIn?.[market] ? { ...v, label: v.labelIn[market] } : v));
  if (role === "superadmin") return inMarket.filter((v) => v.roles?.includes("superadmin_only"));
  if (role === "admin") return inMarket.filter((v) => !v.roles?.includes("superadmin_only"));
  return inMarket.filter((v) => !v.roles || (!v.roles.includes("superadmin_only") && v.roles.includes(role)));
}

export function pinnedViews(role: string): View[] {
  const all = viewsForRole(role);
  const pins = pinsFor(role);
  if (!pins) return [];
  return pins
    .map((h) => {
      if (h.startsWith("section:")) {
        const section = SECTIONS.find((s) => s.key === h.slice("section:".length));
        return section ? sectionEntry(section, role) : null;
      }
      return all.find((v) => v.href === h) ?? null;
    })
    .filter((v): v is View => !!v);
}

/** The rest of a role's menu, grouped by intent, in a stable order. A section is one entry (named for the section
 * when the role sees two or more of its pages, else the one page's own name) and is left out when one of its pages
 * is already pinned, since that page carries the section's tabs. A client's own pages are not listed. */
export function moreViews(role: string): { intent: ViewIntent; title: string; items: View[] }[] {
  const pinned = pinnedViews(role);
  const pinnedHrefs = new Set(pinned.flatMap((v) => v.members ?? [v.href]));
  const pinnedSections = new Set(pinned.flatMap((v) => (v.members ?? [v.href]).map((h) => sectionOf(h)?.key)).filter(Boolean));
  const entries: View[] = [];
  const placed = new Set<string>();
  for (const v of viewsForRole(role)) {
    if (pinnedHrefs.has(v.href) || CLIENT_ONLY.has(v.href)) continue;
    const s = sectionOf(v.href);
    if (!s) {
      entries.push(v);
      continue;
    }
    if (placed.has(s.key) || pinnedSections.has(s.key)) continue;
    placed.add(s.key);
    const entry = sectionEntry(s, role);
    if (entry) entries.push(entry);
  }
  return INTENT_ORDER.map((intent) => ({
    intent,
    title: INTENT_TITLES[intent],
    items: entries.filter((v) => v.intent === intent),
  })).filter((g) => g.items.length > 0);
}

export function isViewActive(pathname: string, href: string): boolean {
  return pathname === href || (href !== "/studio" && pathname.startsWith(href + "/"));
}

/** A menu entry is current on its own page or, for a section, on any of the section's pages. */
export function isEntryActive(pathname: string, item: View): boolean {
  return (item.members ?? [item.href]).some((h) => isViewActive(pathname, h));
}

/** Roles that have conversation tools, and so a workspace. Clients and the platform owner do not. */
export function hasWorkspace(role: string | undefined | null): boolean {
  return !!role && role !== "client" && role !== "superadmin";
}
