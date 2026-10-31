import { firmMarket } from "./format";
// The one list of pages ("Views") in the product, shared by the classic shell's sidebar, the
// Astra workspace rail and the command palette so they cannot disagree about who sees what.
//
// Pages are artifact views the agents open; they are not the navigation model. The primary way
// in is the conversation (/workspace). A role's handful of everyday views are PINNED; everything
// else it may open lives under "More views", grouped by intent, and is reachable by typing in
// the palette. Routes here are stable: never rename or remove one.
import {
  LayoutDashboard, Users, ClipboardCheck, MessageSquareText, ShieldCheck, Sparkles, Settings,
  PlugZap, UserPlus, GitMerge, CalendarClock, FileText, CheckSquare, MessageSquare, Gauge, Activity,
  BarChart3, Workflow, PiggyBank, Scale, Wand2, History, UserCircle, Landmark, Target, Receipt,
  Brain, Timer, TrendingUp, Repeat, UserCheck, LineChart, Banknote, Building2, ArrowLeftRight, Umbrella,
  SlidersHorizontal, BadgeCheck, CalendarCheck,
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
}

export const INTENT_TITLES: Record<ViewIntent, string> = {
  decide: "Decide",
  clients: "Clients & advice",
  operate: "Run the business",
  govern: "Govern the agents",
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
  { href: "/studio/tasks", label: "Tasks", icon: CheckSquare, roles: [A, P], intent: "decide", keywords: ["to do", "todo"] },

  // Clients & advice
  { href: "/studio/clients", label: "Clients", icon: Users, roles: [A, P, PT, R, O, B], intent: "clients", keywords: ["households", "families"] },
  { href: "/studio/ask", label: "Ask your book", icon: MessageSquareText, roles: [A, P, R, B], intent: "clients", keywords: ["question", "search"] },
  { href: "/studio/meetings", label: "Meetings", icon: CalendarClock, roles: [A, P], intent: "clients", keywords: ["prep", "brief", "calendar"] },
  { href: "/studio/reports", label: "Reports", icon: FileText, roles: [A, P, R], intent: "clients", keywords: ["research", "packs"] },
  { href: "/studio/messages", label: "Messages", icon: MessageSquare, roles: [A, P], intent: "clients", keywords: ["client care", "outreach"] },
  { href: "/studio/family", label: "Family aggregate", icon: UserCircle, roles: [A, P, PT], intent: "clients", keywords: ["household wealth", "aggregate"] },
  { href: "/studio/estate", label: "Estate & succession", icon: Landmark, roles: [A, P, PT], intent: "clients", keywords: ["wills", "heirs", "trust"] },
  { href: "/studio/tax", label: "Tax intelligence", icon: Receipt, roles: [A, P, PT], intent: "clients", keywords: ["harvest", "wash sale", "capital gains"] },
  { href: "/studio/licences", label: "Licences", icon: BadgeCheck, roles: [A, P, PT, C, O, B], intent: "clients", market: "IN", keywords: ["sebi", "consent", "investment adviser", "portfolio manager", "pms", "client consent"] },
  { href: "/studio/protection", label: "Protection", icon: Umbrella, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["insurance", "life cover", "disability", "income protection", "tpd", "gap"] },
  { href: "/studio/risk-profiles", label: "Risk profiles", icon: SlidersHorizontal, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["questionnaire", "tolerance", "capacity", "suitability", "risk score"] },
  { href: "/studio/reviews", label: "Client reviews", icon: CalendarCheck, roles: [A, P, PT, C, O, B], intent: "clients", keywords: ["annual review", "cadence", "next review", "overdue", "reverse churning", "ongoing service"] },
  { href: "/studio/behavioural", label: "Behavioural finance", icon: Brain, roles: [A, P, PT], intent: "clients", keywords: ["bias", "nudge"] },
  { href: "/studio/beneficiary-audit", label: "Beneficiary audit", icon: UserCheck, roles: [A, P, PT, C], intent: "clients", keywords: ["registration", "beneficiaries"] },
  { href: "/studio/pipeline", label: "Growth pipeline", icon: TrendingUp, roles: [A, P, PT], intent: "clients", keywords: ["crm", "prospects", "opportunities"] },
  { href: "/canvas", label: "Client view — Canvas", icon: Sparkles, roles: [A, P, B], intent: "clients", keywords: ["client experience", "wealth"] },
  { href: "/canvas/retirement", label: "Client view — Retirement", icon: PiggyBank, roles: [A, P, B], intent: "clients", keywords: ["planner"] },
  { href: "/canvas/goals", label: "Client view — Goals", icon: Target, roles: [A, P, B], intent: "clients" },

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
  { href: "/studio/regulatory-countdown", label: "Regulatory countdown", icon: Timer, roles: [A, P, PT], intent: "govern", keywords: ["deadlines", "rules"] },
  { href: "/studio/agent-history", label: "Agent history", icon: History, roles: [A, PT, C, B], intent: "govern", keywords: ["runs", "activity"] },
  { href: "/studio/workforce", label: "Workforce", icon: Workflow, roles: [A, PT, C, B], intent: "govern", keywords: ["agents", "autonomy", "tier", "kill switch"] },
  { href: "/studio/skills", label: "Skills", icon: Wand2, roles: [A, P, PT], intent: "govern", keywords: ["playbooks"] },

  // Configure
  { href: "/admin", label: "Firm & agents", icon: Settings, roles: [], intent: "configure", keywords: ["users", "branding", "settings", "models", "schedules"] },
  { href: "/admin/foundation", label: "Common foundation", icon: ShieldCheck, roles: [], intent: "configure" },
  { href: "/admin/regulatory", label: "Regulatory", icon: Scale, roles: [C], intent: "configure", keywords: ["frameworks", "rules"] },
  { href: "/admin/connectors", label: "Connectors", icon: PlugZap, roles: [O], intent: "configure", keywords: ["integrations", "custodian"] },

  // Platform
  { href: "/superadmin", label: "Firm management", icon: Building2, roles: ["superadmin_only"], intent: "platform" },
];

/** The handful of views each role opens every day. Everything else is under "More views". */
export const ROLE_PINNED: Record<string, string[]> = {
  adviser: ["/studio/review", "/studio/clients", "/studio/meetings", "/studio/tasks"],
  paraplanner: ["/studio/meetings", "/studio/clients", "/studio/reports", "/studio/tasks"],
  portfolio_team: ["/studio/review", "/studio/clients", "/studio/composites", "/studio/analytics"],
  research_cio: ["/studio/reports", "/studio/composites", "/studio/analytics", "/studio/clients"],
  compliance: ["/provenance", "/studio/review", "/studio/agent-history", "/admin/regulatory"],
  operations: ["/studio/onboarding", "/studio/billing", "/studio/corporate-actions", "/studio/book-integration"],
  branch_leader: ["/studio/capacity", "/studio/billing", "/studio/clients", "/provenance"],
  admin: ["/admin", "/admin/foundation", "/studio/workforce"],
};

/** Every view this role may open. Mirrors the old sidebar's rule exactly. */
export function viewsForRole(role: string): View[] {
  const inMarket = VIEWS.filter((v) => !v.market || v.market === firmMarket());
  if (role === "superadmin") return inMarket.filter((v) => v.roles?.includes("superadmin_only"));
  if (role === "admin") return inMarket.filter((v) => !v.roles?.includes("superadmin_only"));
  return inMarket.filter((v) => !v.roles || (!v.roles.includes("superadmin_only") && v.roles.includes(role)));
}

export function pinnedViews(role: string): View[] {
  const all = viewsForRole(role);
  const pins = ROLE_PINNED[role];
  if (!pins) return [];
  return pins.map((h) => all.find((v) => v.href === h)).filter((v): v is View => !!v);
}

/** The rest of a role's views, grouped by intent, in a stable order. */
export function moreViews(role: string): { intent: ViewIntent; title: string; items: View[] }[] {
  const pinned = new Set(pinnedViews(role).map((v) => v.href));
  const rest = viewsForRole(role).filter((v) => !pinned.has(v.href));
  return INTENT_ORDER.map((intent) => ({
    intent,
    title: INTENT_TITLES[intent],
    items: rest.filter((v) => v.intent === intent),
  })).filter((g) => g.items.length > 0);
}

export function isViewActive(pathname: string, href: string): boolean {
  return pathname === href || (href !== "/studio" && pathname.startsWith(href + "/"));
}

/** Roles that have conversation tools, and so a workspace. Clients and the platform owner do not. */
export function hasWorkspace(role: string | undefined | null): boolean {
  return !!role && role !== "client" && role !== "superadmin";
}
