// The views that open in the workspace's work pane, beside the conversation, instead of as a
// page of their own. The pane's state is the `?view=<route>` query on /workspace and
// /workspace/[id], so an open view is linkable and survives a reload. Only a route that matches
// an entry here opens in the pane; anything else navigates to its classic page as it always has.
// The classic URLs keep working on their own, unchanged.
//
// Adding a view: refactor its page into a view component both the route and the pane import
// (components/views), add it here, and render it in app/workspace/components/WorkPane.tsx.
import { viewsForRole } from "./nav";

export type PaneViewKey =
  | "cockpit" | "recommendations" | "clients" | "client" | "billing" | "valuations" | "onboarding" | "onboarding_case";

interface PaneViewDef {
  key: PaneViewKey;
  /** Matches the route's path (no query). */
  pattern: RegExp;
  /** The lib/nav.ts view whose roles decide who may open it. */
  base: string;
  /** What the pane header calls it until the view names itself (a client's name, say). */
  label: string;
}

export const PANE_VIEWS: PaneViewDef[] = [
  { key: "cockpit", pattern: /^\/studio\/?$/, base: "/studio", label: "Cockpit" },
  { key: "recommendations", pattern: /^\/studio\/review\/?$/, base: "/studio/review", label: "Recommendations" },
  { key: "clients", pattern: /^\/studio\/clients\/?$/, base: "/studio/clients", label: "Clients" },
  { key: "client", pattern: /^\/studio\/clients\/([0-9a-fA-F-]{8,})\/?$/, base: "/studio/clients", label: "Client" },
  { key: "billing", pattern: /^\/studio\/billing\/?$/, base: "/studio/billing", label: "Billing" },
  { key: "valuations", pattern: /^\/studio\/valuations\/?$/, base: "/studio/valuations", label: "Valuations" },
  { key: "onboarding", pattern: /^\/studio\/onboarding\/?$/, base: "/studio/onboarding", label: "Onboarding" },
  { key: "onboarding_case", pattern: /^\/studio\/onboarding\/([0-9a-fA-F-]{8,})\/?$/, base: "/studio/onboarding", label: "Onboarding case" },
];

/** The list a single-item view sits under, so one opened straight from the conversation still has
 * its list behind it in the pane's breadcrumbs. */
export const PANE_PARENT: Partial<Record<PaneViewKey, string>> = {
  client: "/studio/clients",
  onboarding_case: "/studio/onboarding",
};

export interface PaneMatch {
  key: PaneViewKey;
  /** The route as given, path and query - what `?view=` carries and "Open as page" links to. */
  route: string;
  base: string;
  label: string;
  /** The entity the view is about, if any: a household or onboarding case id. */
  id?: string;
  /** Query parameters the view reads (Recommendations: `rec`, the proposal to bring to the top;
   * Billing: `invoice`, the bill to open). */
  query: Record<string, string>;
}

/** The pane view `route` opens, or null when it is not a pane view - or, given a role, when that
 * role's sidebar does not show the page it stands for. */
export function matchPaneView(route: string | null | undefined, role?: string | null): PaneMatch | null {
  if (!route || !route.startsWith("/")) return null;
  const [path, search = ""] = route.split("?", 2);
  for (const def of PANE_VIEWS) {
    const m = def.pattern.exec(path);
    if (!m) continue;
    if (role && !viewsForRole(role).some((v) => v.href === def.base)) return null;
    const query: Record<string, string> = {};
    new URLSearchParams(search).forEach((v, k) => {
      query[k] = v;
    });
    return { key: def.key, route, base: def.base, label: def.label, id: m[1], query };
  }
  return null;
}

/** `?view=` for a route, kept readable: slashes stay slashes, the rest is encoded. */
export function viewQuery(route: string): string {
  return `view=${encodeURIComponent(route).replace(/%2F/gi, "/")}`;
}

/** A workspace path with the pane showing `route` (or closed, for null). */
export function withView(pathname: string, route: string | null): string {
  return route ? `${pathname}?${viewQuery(route)}` : pathname;
}

/** The pane view a tool result can be opened in, if any - for the "Open" affordance on a card.
 * Ids come from the result itself, never from the model's prose. */
export function paneRouteForArtifact(toolKey: string, result: Record<string, any> | null | undefined): string | null {
  const r = result || {};
  switch (toolKey) {
    case "open_view":
      return typeof r.view === "string" ? r.view : null;
    case "read_recommendations": {
      const recs = Array.isArray(r.recommendations) ? r.recommendations : [];
      return recs.length === 1 && recs[0]?.id ? `/studio/review?rec=${recs[0].id}` : "/studio/review";
    }
    case "read_agents":
      return "/studio";
    case "read_billing":
      return r.scope === "household" && typeof r.household_id === "string" ? `/studio/clients/${r.household_id}` : "/studio/billing";
    case "run_billing":
      return "/studio/billing";
    case "read_private_valuations":
    case "record_valuation":
      return "/studio/valuations";
    case "read_onboarding_cases": {
      const cases = Array.isArray(r.cases) ? r.cases : [];
      return cases.length === 1 && cases[0]?.id ? `/studio/onboarding/${cases[0].id}` : "/studio/onboarding";
    }
    case "explain_invoice":
    case "credit_invoice":
      return typeof r.id === "string" ? `/studio/billing?invoice=${r.id}` : "/studio/billing";
    case "search_households": {
      const hits = Array.isArray(r.results) ? r.results : [];
      return hits.length === 1 && hits[0]?.household_id ? `/studio/clients/${hits[0].household_id}` : hits.length ? "/studio/clients" : null;
    }
    default:
      return typeof r.household_id === "string" ? `/studio/clients/${r.household_id}` : null;
  }
}
