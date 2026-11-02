"use client";

import { Component, useMemo } from "react";
import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { ChevronLeft, ChevronRight, CircleAlert, ExternalLink, Maximize2, MessageSquarePlus, Minimize2, X } from "lucide-react";
import { ViewNavProvider, type ViewNav } from "@/components/views/ViewNav";
import { CockpitView } from "@/components/views/CockpitView";
import { RecommendationsView } from "@/components/views/RecommendationsView";
import { ClientsView } from "@/components/views/ClientsView";
import { ClientView } from "@/components/views/ClientView";
import { BillingView } from "@/components/views/BillingView";
import { ValuationsView } from "@/components/views/ValuationsView";
import { OnboardingView } from "@/components/views/OnboardingView";
import { OnboardingCaseView } from "@/components/views/OnboardingCaseView";
import { MeetingsView } from "@/components/views/MeetingsView";
import { MeetingView } from "@/components/views/MeetingView";
import { TasksView } from "@/components/views/TasksView";
import { ReportsView } from "@/components/views/ReportsView";
import { ReportView } from "@/components/views/ReportView";
import { MessagesView } from "@/components/views/MessagesView";
import { MessageThreadView } from "@/components/views/MessageThreadView";
import { IpsView } from "@/components/views/IpsView";
import { ReviewsView } from "@/components/views/ReviewsView";
import { ProtectionView } from "@/components/views/ProtectionView";
import { RiskProfilesView } from "@/components/views/RiskProfilesView";
import { ReconciliationView } from "@/components/views/ReconciliationView";
import { EquityCompView } from "@/components/views/EquityCompView";
import { CashMovementsView } from "@/components/views/CashMovementsView";
import { ClientFileView } from "@/components/views/ClientFileView";
import { ExitsView } from "@/components/views/ExitsView";
import { FinancialPlanView } from "@/components/views/FinancialPlanView";
import { ProductsView } from "@/components/views/ProductsView";
import { TaxDocumentsView, TradesView } from "@/components/views/TradesView";
import { AmlReviewsView } from "@/components/views/AmlReviewsView";
import { ConsentsView } from "@/components/views/ConsentsView";
import { DocumentMatrixView } from "@/components/views/DocumentMatrixView";
import { TransfersView } from "@/components/views/TransfersView";
import { MonitoringView } from "@/components/views/MonitoringView";
import { MoneyInView } from "@/components/views/MoneyInView";
import { AccountFeaturesView } from "@/components/views/AccountFeaturesView";
import { OnboardingScoreboardView } from "@/components/views/OnboardingScoreboardView";
import { OnboardingQueuesView } from "@/components/views/OnboardingQueuesView";
import { NetWorthView } from "@/components/views/NetWorthView";
import { AnalyticsView } from "@/components/views/AnalyticsView";
import { CapacityView } from "@/components/views/CapacityView";
import { AgentHistoryView } from "@/components/views/AgentHistoryView";
import { PipelineView } from "@/components/views/PipelineView";
import { CorporateActionsView } from "@/components/views/CorporateActionsView";
import { ProvenanceView } from "@/components/views/ProvenanceView";
import { CompositesView } from "@/components/views/CompositesView";
import { PricesView } from "@/components/views/PricesView";
import { BeneficiaryAuditView } from "@/components/views/BeneficiaryAuditView";
import { LicencesView } from "@/components/views/LicencesView";
import { TaxView } from "@/components/views/TaxView";
import { EstateView } from "@/components/views/EstateView";
import { FamilyView } from "@/components/views/FamilyView";
import { BehaviouralView } from "@/components/views/BehaviouralView";
import { RegulatoryCountdownView } from "@/components/views/RegulatoryCountdownView";
import { WorkforceView } from "@/components/views/WorkforceView";
import { SkillsView } from "@/components/views/SkillsView";
import { BookIntegrationView } from "@/components/views/BookIntegrationView";
import { BookBatchView } from "@/components/views/BookBatchView";
import { PANE_PARENT, matchPaneView, withView, type PaneMatch } from "@/lib/paneViews";
import { useWorkspace } from "../context";

/** A view that throws stays inside the pane; the conversation beside it keeps working. */
class PaneBoundary extends Component<{ children: ReactNode; route: string }, { failed: string | null }> {
  state = { failed: null as string | null };
  static getDerivedStateFromError(e: unknown) {
    return { failed: e instanceof Error ? e.message : "This view could not be shown" };
  }
  componentDidUpdate(prev: { route: string }) {
    if (prev.route !== this.props.route && this.state.failed) this.setState({ failed: null });
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="flex gap-2 rounded-lg border border-crit/35 bg-crit-bg p-3 text-sm text-ink">
        <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-crit" aria-hidden />
        <span>{this.state.failed}</span>
      </div>
    );
  }
}

function ViewBody({ pane }: { pane: PaneMatch }) {
  switch (pane.key) {
    case "cockpit":
      return <CockpitView />;
    case "recommendations":
      return <RecommendationsView focusId={pane.query.rec ?? null} />;
    case "clients":
      return <ClientsView />;
    case "client":
      return pane.id ? <ClientView key={pane.id} id={pane.id} /> : null;
    case "billing":
      return <BillingView focusInvoice={pane.query.invoice ?? null} />;
    case "valuations":
      return <ValuationsView />;
    case "onboarding":
      return <OnboardingView />;
    case "onboarding_case":
      return pane.id ? <OnboardingCaseView key={pane.id} id={pane.id} /> : null;
    case "meetings":
      return <MeetingsView />;
    case "meeting":
      return pane.id ? <MeetingView key={pane.id} id={pane.id} /> : null;
    case "tasks":
      return <TasksView />;
    case "reports":
      return <ReportsView />;
    case "report":
      return pane.id ? <ReportView key={pane.id} id={pane.id} /> : null;
    case "messages":
      return <MessagesView />;
    case "message_thread":
      return pane.id ? <MessageThreadView key={pane.id} householdId={pane.id} /> : null;
    case "ips":
      return <IpsView household={pane.query.household ?? null} />;
    case "reviews":
      return <ReviewsView household={pane.query.household ?? null} />;
    case "protection":
      return <ProtectionView household={pane.query.household ?? null} />;
    case "risk_profiles":
      return <RiskProfilesView household={pane.query.household ?? null} />;
    case "reconciliation":
      return <ReconciliationView />;
    case "net_worth":
      return <NetWorthView household={pane.query.household ?? null} />;
    case "equity_comp":
      return <EquityCompView household={pane.query.household ?? null} />;
    case "plan":
      return <FinancialPlanView household={pane.query.household ?? null} />;
    case "client_file":
      return <ClientFileView household={pane.query.household ?? null} />;
    case "cash":
      return <CashMovementsView household={pane.query.household ?? null} />;
    case "onboarding_queues":
      return <OnboardingQueuesView />;
    case "onboarding_scoreboard":
      return <OnboardingScoreboardView />;
    case "account_features":
      return <AccountFeaturesView />;
    case "money_in":
      return <MoneyInView />;
    case "monitoring":
      return <MonitoringView />;
    case "transfers":
      return <TransfersView />;
    case "document_matrix":
      return <DocumentMatrixView />;
    case "consents":
      return <ConsentsView household={pane.query.household ?? null} />;
    case "aml":
      return <AmlReviewsView />;
    case "trades":
      return <TradesView household={pane.query.household ?? null} />;
    case "tax_documents":
      return <TaxDocumentsView household={pane.query.household ?? null} />;
    case "products":
      return <ProductsView />;
    case "exits":
      return <ExitsView household={pane.query.household ?? null} />;
    case "analytics":
      return <AnalyticsView />;
    case "capacity":
      return <CapacityView />;
    case "agent_history":
      return <AgentHistoryView />;
    case "pipeline":
      return <PipelineView />;
    case "tax":
      return <TaxView household={pane.query.household ?? null} />;
    case "estate":
      return <EstateView household={pane.query.household ?? null} />;
    case "family":
      return <FamilyView household={pane.query.household ?? null} />;
    case "behavioural":
      return <BehaviouralView household={pane.query.household ?? null} />;
    case "regulatory_countdown":
      return <RegulatoryCountdownView household={pane.query.household ?? null} />;
    case "workforce":
      return <WorkforceView />;
    case "skills":
      return <SkillsView />;
    case "book_integration":
      return <BookIntegrationView />;
    case "book_batch":
      return pane.id ? <BookBatchView key={pane.id} id={pane.id} /> : null;
    case "corporate_actions":
      return <CorporateActionsView />;
    case "provenance":
      return <ProvenanceView />;
    case "composites":
      return <CompositesView />;
    case "prices":
      return <PricesView />;
    case "beneficiary_audit":
      return <BeneficiaryAuditView />;
    case "licences":
      return <LicencesView />;
  }
}

const iconBtn =
  "flex h-7 w-7 shrink-0 items-center justify-center rounded text-ink-muted transition-colors hover:bg-border-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

/** The work pane: the view the agents (or you) opened, beside the conversation. Its header names
 * what is open, walks back through what was opened inside it, and can hand it to the conversation
 * ("Ask about this"), widen it over the conversation, open it as its own page, or close it. */
export function WorkPane({ pane }: { pane: PaneMatch }) {
  const pathname = usePathname();
  const {
    openView, closeView, trail, titles, setViewTitle, focus, setFocus, say, setSheetHidden, setContextChip,
  } = useWorkspace();

  const route = pane.route;
  const nav = useMemo<ViewNav>(
    () => ({
      inPane: true,
      open: (r) => openView(r, { within: true }),
      href: (r) => withView(pathname, r),
      say,
      changed: () => window.dispatchEvent(new Event("astra:changed")),
      setTitle: (t) => setViewTitle(route, t),
    }),
    [openView, pathname, say, setViewTitle, route]
  );

  // A client or a case opened straight from the conversation still has its list behind it.
  const { crumbs, synthetic } = useMemo(() => {
    const base = trail.length && trail[trail.length - 1] === route ? trail : [route];
    const first = matchPaneView(base[0]);
    const parent = first ? PANE_PARENT[first.key] : undefined;
    return parent
      ? { crumbs: [parent, ...base], synthetic: true }
      : { crumbs: base, synthetic: false };
  }, [trail, route]);
  const titleOf = (r: string) => titles[r] || matchPaneView(r)?.label || r;
  const back = crumbs.length > 1 ? crumbs[crumbs.length - 2] : null;
  // The list put in front of a client is not on the trail: going to it starts the trail again.
  const goCrumb = (i: number) => openView(crumbs[i], { within: !(synthetic && i === 0) });
  const title = titleOf(route);

  return (
    <section aria-label={`${title} - open beside the conversation`} data-testid="work-pane" data-view={pane.key}
      className="flex h-full min-h-0 flex-col bg-paper">
      <header className="shrink-0 border-b border-border">
        <div className="flex h-12 items-center gap-1 px-2">
          {/* Below md the pane is a sheet over the conversation: back to it, the view stays open. */}
          <button
            type="button"
            onClick={() => setSheetHidden(true)}
            data-testid="pane-to-conversation"
            className={`${iconBtn} lg:hidden`}
            aria-label="Back to the conversation"
            title="Back to the conversation"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          {back && (
            <button
              type="button"
              onClick={() => goCrumb(crumbs.length - 2)}
              data-testid="pane-back"
              className={iconBtn}
              aria-label={`Back to ${titleOf(back)}`}
              title={`Back to ${titleOf(back)}`}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          )}
          <h2 className="min-w-0 flex-1 truncate px-1 text-sm font-medium text-ink" data-testid="pane-title">{title}</h2>
          <button
            type="button"
            data-testid="pane-ask"
            onClick={() => {
              setContextChip({ route, label: matchPaneView(route)?.label || title, id: pane.id ?? pane.query.rec ?? pane.query.invoice ?? pane.query.household });
              setSheetHidden(true);
            }}
            className="flex h-7 shrink-0 items-center gap-1 rounded px-2 text-xs text-ink-muted transition-colors hover:bg-border-soft hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            title="Ask Astra about this view"
          >
            <MessageSquarePlus className="h-3.5 w-3.5" aria-hidden />
            <span className="hidden sm:inline">Ask about this</span>
          </button>
          <a href={route} data-testid="pane-open-page" className={iconBtn} aria-label="Open as page" title="Open as page">
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
          <button
            type="button"
            onClick={() => setFocus(!focus)}
            data-testid="pane-focus"
            aria-pressed={focus}
            className={`${iconBtn} hidden lg:flex`}
            aria-label={focus ? "Show the conversation" : "Widen over the conversation"}
            title={focus ? "Show the conversation" : "Widen over the conversation"}
          >
            {focus ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
          <button type="button" onClick={closeView} data-testid="pane-close" className={iconBtn} aria-label="Close the view" title="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        {crumbs.length > 1 && (
          <nav aria-label="In this pane" className="flex min-w-0 items-center gap-1 overflow-x-auto px-3 pb-2 text-xs text-ink-muted" data-testid="pane-breadcrumbs">
            {crumbs.map((r, i) => (
              <span key={`${r}-${i}`} className="flex shrink-0 items-center gap-1">
                {i > 0 && <ChevronRight className="h-3 w-3" aria-hidden />}
                {i < crumbs.length - 1 ? (
                  <button type="button" onClick={() => goCrumb(i)} className="rounded hover:text-ink hover:underline">
                    {titleOf(r)}
                  </button>
                ) : (
                  <span className="text-ink" aria-current="page">{titleOf(r)}</span>
                )}
              </span>
            ))}
          </nav>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="px-4 py-4 sm:px-5">
          <ViewNavProvider value={nav}>
            <PaneBoundary route={route}>
              <ViewBody pane={pane} />
            </PaneBoundary>
          </ViewNavProvider>
        </div>
      </div>
    </section>
  );
}
