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
  | "cockpit" | "recommendations" | "clients" | "client" | "billing" | "valuations" | "onboarding" | "onboarding_case"
  | "meetings" | "meeting" | "tasks" | "reports" | "report" | "messages" | "message_thread" | "ips"
  | "reviews" | "protection" | "risk_profiles" | "reconciliation" | "net_worth" | "exits" | "products" | "equity_comp" | "plan" | "client_file" | "cash" | "trades" | "tax_documents" | "aml" | "consents" | "onboarding_queues"
  | "analytics" | "capacity" | "agent_history" | "provenance" | "composites" | "prices" | "beneficiary_audit" | "licences"
  | "corporate_actions" | "pipeline" | "tax" | "estate" | "family" | "behavioural" | "regulatory_countdown" | "workforce"
  | "skills" | "book_integration" | "book_batch";

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
  { key: "meetings", pattern: /^\/studio\/meetings\/?$/, base: "/studio/meetings", label: "Meetings" },
  { key: "meeting", pattern: /^\/studio\/meetings\/([0-9a-fA-F-]{8,})\/?$/, base: "/studio/meetings", label: "Meeting" },
  { key: "tasks", pattern: /^\/studio\/tasks\/?$/, base: "/studio/tasks", label: "Tasks" },
  { key: "reports", pattern: /^\/studio\/reports\/?$/, base: "/studio/reports", label: "Reports" },
  { key: "report", pattern: /^\/studio\/reports\/([0-9a-fA-F-]{8,})\/?$/, base: "/studio/reports", label: "Report" },
  { key: "messages", pattern: /^\/studio\/messages\/?$/, base: "/studio/messages", label: "Messages" },
  { key: "message_thread", pattern: /^\/studio\/messages\/([0-9a-fA-F-]{8,})\/?$/, base: "/studio/messages", label: "Messages" },
  { key: "ips", pattern: /^\/studio\/ips\/?$/, base: "/studio/ips", label: "IPS monitoring" },
  { key: "reviews", pattern: /^\/studio\/reviews\/?$/, base: "/studio/reviews", label: "Client reviews" },
  { key: "protection", pattern: /^\/studio\/protection\/?$/, base: "/studio/protection", label: "Protection" },
  { key: "risk_profiles", pattern: /^\/studio\/risk-profiles\/?$/, base: "/studio/risk-profiles", label: "Risk profiles" },
  { key: "reconciliation", pattern: /^\/studio\/reconciliation\/?$/, base: "/studio/reconciliation", label: "Reconciliation" },
  { key: "net_worth", pattern: /^\/studio\/net-worth\/?$/, base: "/studio/net-worth", label: "Net worth" },
  { key: "exits", pattern: /^\/studio\/exits\/?$/, base: "/studio/exits", label: "Departures and estates" },
  { key: "products", pattern: /^\/studio\/products\/?$/, base: "/studio/products", label: "Product shelf" },
  { key: "equity_comp", pattern: /^\/studio\/equity-comp\/?$/, base: "/studio/equity-comp", label: "Equity compensation" },
  { key: "plan", pattern: /^\/studio\/plan\/?$/, base: "/studio/plan", label: "Financial plan" },
  { key: "client_file", pattern: /^\/studio\/client-file\/?$/, base: "/studio/client-file", label: "Client file" },
  { key: "cash", pattern: /^\/studio\/cash\/?$/, base: "/studio/cash", label: "Cash movements" },
  { key: "trades", pattern: /^\/studio\/trades\/?$/, base: "/studio/trades", label: "Trades" },
  { key: "tax_documents", pattern: /^\/studio\/tax-documents\/?$/, base: "/studio/tax-documents", label: "Tax documents" },
  { key: "aml", pattern: /^\/studio\/aml\/?$/, base: "/studio/aml", label: "AML reviews" },
  { key: "consents", pattern: /^\/studio\/consents\/?$/, base: "/studio/consents", label: "Consents" },
  { key: "onboarding_queues", pattern: /^\/studio\/onboarding-queues\/?$/, base: "/studio/onboarding-queues", label: "Onboarding queues" },
  { key: "analytics", pattern: /^\/studio\/analytics\/?$/, base: "/studio/analytics", label: "Analytics" },
  { key: "capacity", pattern: /^\/studio\/capacity\/?$/, base: "/studio/capacity", label: "Capacity & outcomes" },
  { key: "agent_history", pattern: /^\/studio\/agent-history\/?$/, base: "/studio/agent-history", label: "Agent history" },
  { key: "provenance", pattern: /^\/provenance\/?$/, base: "/provenance", label: "Provenance" },
  { key: "composites", pattern: /^\/studio\/composites\/?$/, base: "/studio/composites", label: "Composites" },
  { key: "prices", pattern: /^\/studio\/prices\/?$/, base: "/studio/prices", label: "Prices" },
  { key: "beneficiary_audit", pattern: /^\/studio\/beneficiary-audit\/?$/, base: "/studio/beneficiary-audit", label: "Beneficiary audit" },
  { key: "licences", pattern: /^\/studio\/licences\/?$/, base: "/studio/licences", label: "Licences" },
  { key: "corporate_actions", pattern: /^\/studio\/corporate-actions\/?$/, base: "/studio/corporate-actions", label: "Corporate actions" },
  { key: "pipeline", pattern: /^\/studio\/pipeline\/?$/, base: "/studio/pipeline", label: "Growth pipeline" },
  { key: "tax", pattern: /^\/studio\/tax\/?$/, base: "/studio/tax", label: "Tax intelligence" },
  { key: "estate", pattern: /^\/studio\/estate\/?$/, base: "/studio/estate", label: "Estate & succession" },
  { key: "family", pattern: /^\/studio\/family\/?$/, base: "/studio/family", label: "Family aggregate" },
  { key: "behavioural", pattern: /^\/studio\/behavioural\/?$/, base: "/studio/behavioural", label: "Behavioural finance" },
  { key: "regulatory_countdown", pattern: /^\/studio\/regulatory-countdown\/?$/, base: "/studio/regulatory-countdown", label: "Regulatory countdown" },
  { key: "workforce", pattern: /^\/studio\/workforce\/?$/, base: "/studio/workforce", label: "Workforce" },
  { key: "skills", pattern: /^\/studio\/skills\/?$/, base: "/studio/skills", label: "Skills" },
  { key: "book_integration", pattern: /^\/studio\/book-integration\/?$/, base: "/studio/book-integration", label: "Book integration" },
  { key: "book_batch", pattern: /^\/studio\/book-integration\/([0-9a-fA-F-]{8,})\/?$/, base: "/studio/book-integration", label: "Acquired book" },
];

/** The list a single-item view sits under, so one opened straight from the conversation still has
 * its list behind it in the pane's breadcrumbs. */
export const PANE_PARENT: Partial<Record<PaneViewKey, string>> = {
  client: "/studio/clients",
  onboarding_case: "/studio/onboarding",
  meeting: "/studio/meetings",
  report: "/studio/reports",
  message_thread: "/studio/messages",
  book_batch: "/studio/book-integration",
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
   * Billing: `invoice`, the bill to open; a view of every client: `household`, the one to show). */
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
  // A result about one client opens the view on that client, not on everyone.
  const onClient = (route: string) => (typeof r.household_id === "string" ? `${route}?household=${r.household_id}` : route);
  switch (toolKey) {
    case "open_view":
      return typeof r.view === "string" ? r.view : null;
    case "read_recommendations": {
      const recs = Array.isArray(r.recommendations) ? r.recommendations : [];
      return recs.length === 1 && recs[0]?.id ? `/studio/review?rec=${recs[0].id}` : "/studio/review";
    }
    case "read_agents":
      return "/studio/workforce";
    case "read_tax_book":
      return "/studio/tax";
    case "read_tax_position":
    case "check_household_wash_sale":
      return onClient("/studio/tax");
    case "read_estate":
      return onClient("/studio/estate");
    case "read_family":
      return onClient("/studio/family");
    case "read_behavioural":
      return onClient("/studio/behavioural");
    case "read_regulatory_countdown":
      return onClient("/studio/regulatory-countdown");
    case "read_skills":
    case "create_skill":
    case "update_skill":
    case "delete_skill":
    case "clone_skill":
      return "/studio/skills";
    case "run_skill":
      return "/studio/review";
    case "read_book_batches":
    case "start_book_batch":
      return r.batch && typeof r.batch.id === "string" ? `/studio/book-integration/${r.batch.id}` : "/studio/book-integration";
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
    case "read_meetings": {
      const ms = Array.isArray(r.meetings) ? r.meetings : [];
      return ms.length === 1 && ms[0]?.id ? `/studio/meetings/${ms[0].id}` : "/studio/meetings";
    }
    case "read_tasks":
    case "complete_task":
    case "assign_task":
    case "create_task":
      return "/studio/tasks";
    case "read_reports":
    case "withdraw_report": {
      const rs = Array.isArray(r.reports) ? r.reports : [];
      return rs.length === 1 && rs[0]?.id ? `/studio/reports/${rs[0].id}` : "/studio/reports";
    }
    case "read_messages":
    case "send_client_message":
      return r.scope === "household" && typeof r.household_id === "string" ? `/studio/messages/${r.household_id}` : "/studio/messages";
    case "read_ips_monitoring":
    case "record_ips":
      return onClient("/studio/ips");
    case "read_client_reviews":
    case "record_client_review":
    case "set_review_cadence":
    case "book_client_review":
      return onClient("/studio/reviews");
    case "read_protection":
    case "record_protection_policy":
    case "end_protection_policy":
    case "record_income":
      return onClient("/studio/protection");
    case "read_risk_profiles":
    case "decide_risk_profile":
      return onClient("/studio/risk-profiles");
    case "read_reconciliation":
    case "import_custody_statement":
    case "resolve_recon_break":
      return "/studio/reconciliation";
    case "read_balance_sheet":
    case "record_balance_item":
    case "end_balance_item":
      return onClient("/studio/net-worth");
    case "read_consents":
    case "record_consent":
      return onClient("/studio/consents");
    case "read_onboarding_queues":
      return "/studio/onboarding-queues";
    case "read_aml_reviews":
    case "record_screening_decision":
    case "sign_off_edd":
      return "/studio/aml";
    case "read_trades":
      return onClient("/studio/trades");
    case "read_tax_documents":
    case "record_tax_document":
      return onClient("/studio/tax-documents");
    case "read_cash_movements":
      return onClient("/studio/cash");
    case "read_client_file":
    case "record_trusted_contact":
    case "record_estate_document":
    case "record_life_event":
      return onClient("/studio/client-file");
    case "read_financial_plan":
    case "record_income_stream":
    case "end_income_stream":
    case "record_education_goal":
      return onClient("/studio/plan");
    case "read_equity_comp":
    case "record_equity_grant":
    case "end_equity_grant":
    case "record_concentration_plan":
      return onClient("/studio/equity-comp");
    case "read_product_shelf":
    case "record_product_decision":
    case "set_approved_products_only":
      return "/studio/products";
    case "read_client_exits":
    case "start_client_departure":
    case "record_client_death":
    case "record_executor":
    case "record_account_transfer":
    case "close_client_exit":
    case "withdraw_client_exit":
      return onClient("/studio/exits");
    case "read_analytics":
      return "/studio/analytics";
    case "read_capacity":
      return "/studio/capacity";
    case "read_agent_history":
      return "/studio/agent-history";
    case "read_provenance":
    case "resolve_surveillance_flag":
    case "escalate_surveillance_flag":
    case "evaluate_agents":
      return "/provenance";
    case "read_composites":
    case "read_composite_report":
    case "read_firm_definition_check":
      return "/studio/composites";
    case "read_price_freshness":
    case "refresh_prices":
      return "/studio/prices";
    case "read_beneficiary_audit":
      return "/studio/beneficiary-audit";
    case "read_licences":
      return "/studio/licences";
    case "read_corporate_actions":
    case "compute_corporate_action_entitlements":
    case "post_corporate_action_entitlement":
      return "/studio/corporate-actions";
    case "read_crm_pipeline":
    case "create_crm_contact":
    case "create_crm_opportunity":
    case "log_crm_activity":
    case "update_crm_opportunity_stage":
      return "/studio/pipeline";
    case "explain_invoice":
    case "credit_invoice":
      return typeof r.id === "string" ? `/studio/billing?invoice=${r.id}` : "/studio/billing";
    case "screen_book": {
      const hs = Array.isArray(r.households) ? r.households : [];
      return hs.length === 1 && hs[0]?.household_id ? `/studio/clients/${hs[0].household_id}` : "/studio/clients";
    }
    case "search_households": {
      const hits = Array.isArray(r.results) ? r.results : [];
      return hits.length === 1 && hits[0]?.household_id ? `/studio/clients/${hits[0].household_id}` : hits.length ? "/studio/clients" : null;
    }
    default:
      return typeof r.household_id === "string" ? `/studio/clients/${r.household_id}` : null;
  }
}
