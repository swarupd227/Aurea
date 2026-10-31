import { PortfolioCard } from "./PortfolioCard";
import { OrdersCard } from "./OrdersCard";
import { CompliancePanelCard } from "./CompliancePanelCard";
import { CrmPipelineCard } from "./CrmPipelineCard";
import { CorporateActionsCard } from "./CorporateActionsCard";
import { SleevesCard } from "./SleevesCard";
import { SleeveNettingCard } from "./SleeveNettingCard";
import { WashSaleCalendarCard } from "./WashSaleCalendarCard";
import { PerformanceCard } from "./PerformanceCard";
import { BillingCard } from "./BillingCard";
import { ValuationCard } from "./ValuationCard";
import { PriceFreshnessCard } from "./PriceFreshnessCard";
import { ReconciliationCard } from "./ReconciliationCard";
import { ProtectionCard } from "./ProtectionCard";
import { InvoiceCard } from "./InvoiceCard";
import { AccountRegistrationCard } from "./AccountRegistrationCard";
import { BeneficiaryAuditCard } from "./BeneficiaryAuditCard";
import { AIGovernanceCard } from "./AIGovernanceCard";
import { CompositesCard } from "./CompositesCard";
import { CompositeReportCard } from "./CompositeReportCard";
import { FirmDefinitionCheckCard } from "./FirmDefinitionCheckCard";
import { RecommendationsCard } from "./RecommendationsCard";
import { AgentsCard } from "./AgentsCard";
import { AgentRunCard, isAgentRunData } from "./AgentRunCard";
import {
  AgentHistoryCard, AnalyticsCard, CapacityCard, OnboardingCasesCard, ProvenanceCard, ReportsCard, TasksCard,
} from "./PageReadCards";
import type { Artifact, MessageCard } from "../../types";

/**
 * A card that belongs to a message itself, not to a tool call - today, a run narrated into the
 * thread by the agent that ran it. Same defensive rule: a shape that does not match renders nothing.
 */
export function MessageCardRenderer({ card }: { card: MessageCard }) {
  try {
    if (card.kind === "agent_run" && isAgentRunData(card.data)) return <AgentRunCard data={card.data} />;
    return null;
  } catch {
    return null;
  }
}

/**
 * One artifact per completed tool call. A tool_key without a card here renders
 * nothing — the prose answer already covers it, and a raw JSON dump would be
 * noise, not information. Wrapped defensively: a shape that doesn't match what
 * a card expects (e.g. an executor's stub/error path) is skipped rather than
 * crashing the message.
 */
export function ArtifactRenderer({ artifact }: { artifact: Artifact }) {
  return artifactCard(artifact);
}

/** The card for one tool result, or null when there is none - so a message can tell which of its
 * results drew a card (and put an "Open" on it) and which did not. */
export function artifactCard(artifact: Artifact) {
  try {
    switch (artifact.tool_key) {
      case "read_portfolio":
        if (!Array.isArray(artifact.result?.holdings)) return null;
        return <PortfolioCard result={artifact.result as any} />;
      case "execute_orders":
        if (!Array.isArray(artifact.result?.fills)) return null;
        return <OrdersCard result={artifact.result as any} />;
      case "read_compliance_program":
        if (!Array.isArray(artifact.result?.conflicts)) return null;
        return <CompliancePanelCard result={artifact.result as any} />;
      case "read_crm_pipeline":
        if (!Array.isArray(artifact.result?.opportunities)) return null;
        return <CrmPipelineCard result={artifact.result as any} />;
      case "read_corporate_actions":
        if (!Array.isArray(artifact.result?.actions)) return null;
        return <CorporateActionsCard result={artifact.result as any} />;
      case "read_sleeves":
        if (!Array.isArray(artifact.result?.sleeves)) return null;
        return <SleevesCard result={artifact.result as any} />;
      case "net_sleeve_intents":
        if (!Array.isArray(artifact.result?.net_orders)) return null;
        return <SleeveNettingCard result={artifact.result as any} />;
      case "check_household_wash_sale":
        if (!Array.isArray(artifact.result?.violations)) return null;
        return <WashSaleCalendarCard result={artifact.result as any} />;
      case "read_private_valuations":
        if (!Array.isArray(artifact.result?.instruments)) return null;
        return <ValuationCard result={artifact.result as any} />;
      case "record_valuation":
        if (typeof artifact.result?.unit_value !== "number") return null;
        return <ValuationCard result={artifact.result as any} />;
      case "read_price_freshness":
        if (!Array.isArray(artifact.result?.instruments)) return null;
        return <PriceFreshnessCard result={artifact.result as any} />;
      case "refresh_prices":
        if (typeof artifact.result?.resolved !== "number") return null;
        return <PriceFreshnessCard result={artifact.result as any} />;
      case "read_protection":
        if (!Array.isArray(artifact.result?.households) && !Array.isArray(artifact.result?.people)) return null;
        return <ProtectionCard result={artifact.result as any} />;
      case "record_protection_policy":
      case "end_protection_policy":
      case "record_income":
        if (!artifact.result?.policy_id && !artifact.result?.person_id) return null;
        return <ProtectionCard result={artifact.result as any} />;
      case "read_reconciliation":
        if (!Array.isArray(artifact.result?.accounts) || !Array.isArray(artifact.result?.breaks)) return null;
        return <ReconciliationCard result={artifact.result as any} />;
      case "import_custody_statement":
        if (typeof artifact.result?.positions_compared !== "number") return null;
        return <ReconciliationCard result={artifact.result as any} />;
      case "resolve_recon_break":
        if (typeof artifact.result?.status !== "string" || typeof artifact.result?.was !== "string") return null;
        return <ReconciliationCard result={artifact.result as any} />;
      case "read_billing":
      case "run_billing":
      case "credit_invoice":
        if (!Array.isArray(artifact.result?.invoices)) return null;
        return <BillingCard result={artifact.result as any} />;
      case "explain_invoice":
        if (!artifact.result?.calculation) return null;
        return <InvoiceCard result={artifact.result as any} />;
      case "read_performance":
        if (!Array.isArray(artifact.result?.periods)) return null;
        return <PerformanceCard result={artifact.result as any} />;
      case "read_account_registration":
        if (!Array.isArray(artifact.result?.beneficiaries)) return null;
        return <AccountRegistrationCard result={artifact.result as any} />;
      case "read_beneficiary_audit":
        if (!Array.isArray(artifact.result?.gaps)) return null;
        return <BeneficiaryAuditCard result={artifact.result as any} />;
      case "read_ai_governance_summary":
        if (!Array.isArray(artifact.result?.overdue_review)) return null;
        return <AIGovernanceCard result={artifact.result as any} />;
      case "read_composites":
        if (!Array.isArray(artifact.result?.composites)) return null;
        return <CompositesCard result={artifact.result as any} />;
      case "read_composite_report":
        if (!artifact.result?.ex_post_std_dev) return null;
        return <CompositeReportCard result={artifact.result as any} />;
      case "read_recommendations":
        if (!Array.isArray(artifact.result?.recommendations)) return null;
        return <RecommendationsCard result={artifact.result as any} />;
      case "read_agent_history":
        if (!Array.isArray(artifact.result?.runs) || typeof artifact.result?.by_status !== "object") return null;
        return <AgentHistoryCard result={artifact.result as any} />;
      case "read_capacity":
        if (typeof artifact.result?.total_agent_runs !== "number" || !artifact.result?.estimate || !artifact.result?.recommendations_by_status) return null;
        return <CapacityCard result={artifact.result as any} />;
      case "read_analytics":
        if (!artifact.result?.headline || typeof artifact.result.headline !== "object") return null;
        return <AnalyticsCard result={{ bases: {}, currency: "", ...artifact.result } as any} />;
      case "read_onboarding_cases":
        if (!Array.isArray(artifact.result?.cases) || !artifact.result?.summary || !artifact.result.cases.every((c: any) => Array.isArray(c?.tracks))) return null;
        return <OnboardingCasesCard result={artifact.result as any} />;
      case "read_tasks":
      case "complete_task":
      case "assign_task":
      case "create_task":
        if (!Array.isArray(artifact.result?.tasks)) return null;
        return <TasksCard result={artifact.result as any} />;
      case "read_reports":
      case "withdraw_report":
        if (!Array.isArray(artifact.result?.reports)) return null;
        return <ReportsCard result={artifact.result as any} />;
      case "read_provenance":
        if (typeof artifact.result?.ledger?.valid !== "boolean" || !Array.isArray(artifact.result?.surveillance?.flags) || !artifact.result?.surveillance?.by_severity || !Array.isArray(artifact.result?.evaluations) || !Array.isArray(artifact.result?.autonomy_changes)) return null;
        return <ProvenanceCard result={artifact.result as any} />;
      case "read_agents":
        if (!Array.isArray(artifact.result?.agents)) return null;
        return <AgentsCard result={artifact.result as any} />;
      case "read_firm_definition_check":
        if (typeof artifact.result?.clean !== "boolean") return null;
        return <FirmDefinitionCheckCard result={artifact.result as any} />;
      default:
        return null;
    }
  } catch {
    return null;
  }
}
