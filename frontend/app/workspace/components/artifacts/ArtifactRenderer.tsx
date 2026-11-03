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
import { LicenceCard } from "./LicenceCard";
import { TaxPositionCard } from "./TaxPositionCard";
import { RiskProfileCard } from "./RiskProfileCard";
import { ReviewsCard } from "./ReviewsCard";
import { IpsCard } from "./IpsCard";
import { BalanceSheetCard } from "./BalanceSheetCard";
import { EquityCompCard } from "./EquityCompCard";
import { CashCard } from "./CashCard";
import { ClientFileCard } from "./ClientFileCard";
import { ExitsCard } from "./ExitsCard";
import { PlanCard } from "./PlanCard";
import { ShelfCard } from "./ShelfCard";
import { TaxDocumentsCard, TradesCard } from "./TradesCard";
import { AmlCard } from "./AmlCard";
import { ConsentsCard } from "./ConsentsCard";
import { DocumentMatrixCard } from "./DocumentMatrixCard";
import { TransfersCard } from "./TransfersCard";
import { MonitoringCard } from "./MonitoringCard";
import { MoneyInCard } from "./MoneyInCard";
import { AccountFeaturesCard } from "./AccountFeaturesCard";
import { OnboardingScoreboardCard } from "./OnboardingScoreboardCard";
import { RepaperingCard } from "./RepaperingCard";
import { MyDayCard } from "./MyDayCard";
import { AdvisersCard } from "./AdvisersCard";
import { PoliciesCard } from "./PoliciesCard";
import { OnboardingQueuesCard } from "./OnboardingQueuesCard";
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
import { BookScreenCard } from "./BookScreenCard";
import { TaxBookCard, isTaxBook } from "./TaxBookCard";
import { EstateCard, isEstate } from "./EstateCard";
import { ClientCanvasCard, isClientCanvas } from "./ClientCanvasCard";
import { MyNetWorthCard, isMyNetWorth } from "./MyNetWorthCard";
import { MyIpsCard, isMyIps } from "./MyIpsCard";
import { MyRetirementCard, isMyRetirement } from "./MyRetirementCard";
import { MyGoalsCard, isMyGoals } from "./MyGoalsCard";
import { MyFeesCard, isMyFees } from "./MyFeesCard";
import { MyReportCard, isMyReport } from "./MyReportCard";
import { QuestionnaireRecordedCard, isQuestionnaireRecorded } from "./QuestionnaireRecordedCard";
import { FamilyCard, isFamily } from "./FamilyCard";
import { BehaviouralCard, isBehavioural } from "./BehaviouralCard";
import { RegulatoryCountdownCard, isRegulatoryCountdown } from "./RegulatoryCountdownCard";
import { SkillsCard, isSkills } from "./SkillsCard";
import { BookBatchCard, isBookBatch } from "./BookBatchCard";
import {
  AgentHistoryCard, AnalyticsCard, CapacityCard, MessagesCard, OnboardingCasesCard, ProvenanceCard, ReportsCard, TasksCard,
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
      case "read_risk_profiles":
        if (!Array.isArray(artifact.result?.households) && !Array.isArray(artifact.result?.mandates)) return null;
        return <RiskProfileCard result={artifact.result as any} />;
      case "decide_risk_profile":
        if (typeof artifact.result?.decision !== "string") return null;
        return <RiskProfileCard result={artifact.result as any} />;
      case "read_client_reviews":
      case "record_client_review":
      case "set_review_cadence":
      case "book_client_review":
        if (!Array.isArray(artifact.result?.households) && typeof artifact.result?.status !== "string") return null;
        return <ReviewsCard result={artifact.result as any} />;
      case "read_ips_monitoring":
      case "record_ips":
        if (!Array.isArray(artifact.result?.mandates) && typeof artifact.result?.status !== "string") return null;
        return <IpsCard result={artifact.result as any} />;
      case "read_balance_sheet":
      case "record_balance_item":
      case "end_balance_item":
        if (!Array.isArray(artifact.result?.households) && typeof artifact.result?.net_worth !== "number") return null;
        return <BalanceSheetCard result={artifact.result as any} />;
      case "read_repapering":
      case "start_repapering":
      case "record_repapering":
        if (!Array.isArray(artifact.result?.campaigns)) return null;
        return <RepaperingCard result={artifact.result as any} />;
      case "read_my_day":
        if (!artifact.result?.waiting) return null;
        return <MyDayCard result={artifact.result as any} />;
      case "read_autonomy_policies":
      case "set_autonomy_policy":
        if (!Array.isArray(artifact.result?.policies)) return null;
        return <PoliciesCard result={artifact.result as any} />;
      case "read_advisers":
      case "assign_adviser":
        if (!Array.isArray(artifact.result?.advisers)) return null;
        return <AdvisersCard result={artifact.result as any} />;
      case "read_onboarding_scoreboard":
      case "record_onboarding_feedback":
        if (!artifact.result?.days_to_invest) return null;
        return <OnboardingScoreboardCard result={artifact.result as any} />;
      case "read_account_features":
      case "request_account_feature":
      case "decide_account_feature":
      case "record_senior_hold":
        if (!Array.isArray(artifact.result?.features)) return null;
        return <AccountFeaturesCard result={artifact.result as any} />;
      case "read_money_in":
      case "record_bank_link":
      case "record_deposit":
      case "revalidate_standing_instruction":
        if (!Array.isArray(artifact.result?.deposits)) return null;
        return <MoneyInCard result={artifact.result as any} />;
      case "read_transaction_monitoring":
      case "decide_monitoring_alert":
      case "record_investigation_step":
        if (!Array.isArray(artifact.result?.alerts)) return null;
        return <MonitoringCard result={artifact.result as any} />;
      case "read_transfer_files":
      case "record_transfer_event":
      case "record_private_transfer_item":
      case "record_rollover_facts":
        if (!Array.isArray(artifact.result?.transfers)) return null;
        return <TransfersCard result={artifact.result as any} />;
      case "read_document_matrix":
      case "record_registration_detail":
      case "set_custodian_documents":
        if (!Array.isArray(artifact.result?.registrations)) return null;
        return <DocumentMatrixCard result={artifact.result as any} />;
      case "read_consents":
      case "record_consent":
        if (!Array.isArray(artifact.result?.records)) return null;
        return <ConsentsCard result={artifact.result as any} />;
      case "read_onboarding_queues":
        if (!artifact.result?.queues) return null;
        return <OnboardingQueuesCard result={artifact.result as any} />;
      case "read_aml_reviews":
      case "record_screening_decision":
      case "sign_off_edd":
        if (!Array.isArray(artifact.result?.hits)) return null;
        return <AmlCard result={artifact.result as any} />;
      case "read_trades":
        if (!Array.isArray(artifact.result?.trades)) return null;
        return <TradesCard result={artifact.result as any} />;
      case "read_tax_documents":
      case "record_tax_document":
        if (!Array.isArray(artifact.result?.households) && !Array.isArray(artifact.result?.expected)) return null;
        return <TaxDocumentsCard result={artifact.result as any} />;
      case "read_cash_movements":
      case "request_cash_movement":
      case "record_cash_paid":
      case "cancel_cash_request":
      case "record_standing_instruction":
      case "end_standing_instruction":
      case "start_phase_in":
      case "end_phase_in":
        if (!Array.isArray(artifact.result?.requests)) return null;
        return <CashCard result={artifact.result as any} />;
      case "read_client_file":
      case "record_trusted_contact":
      case "record_estate_document":
      case "record_life_event":
        if (!Array.isArray(artifact.result?.households) && !Array.isArray(artifact.result?.people)) return null;
        return <ClientFileCard result={artifact.result as any} />;
      case "read_financial_plan":
      case "record_income_stream":
      case "end_income_stream":
      case "record_education_goal":
        if (!artifact.result?.cash) return null;
        return <PlanCard result={artifact.result as any} />;
      case "read_equity_comp":
      case "record_equity_grant":
      case "end_equity_grant":
      case "record_concentration_plan":
        if (!Array.isArray(artifact.result?.households) && !Array.isArray(artifact.result?.grants)) return null;
        return <EquityCompCard result={artifact.result as any} />;
      case "read_product_shelf":
      case "record_product_decision":
      case "set_approved_products_only":
        if (!Array.isArray(artifact.result?.products)) return null;
        return <ShelfCard result={artifact.result as any} />;
      case "read_client_exits":
      case "start_client_departure":
      case "record_client_death":
      case "record_executor":
      case "record_account_transfer":
      case "close_client_exit":
      case "withdraw_client_exit":
        if (!Array.isArray(artifact.result?.exits) && typeof artifact.result?.status !== "string") return null;
        return <ExitsCard result={artifact.result as any} />;
      case "read_tax_position":
        if (!Array.isArray(artifact.result?.taxpayers)) return null;
        return <TaxPositionCard result={artifact.result as any} />;
      case "read_licences":
        if (!artifact.result?.applies || !Array.isArray(artifact.result?.licences)) return null;
        return <LicenceCard result={artifact.result as any} />;
      case "record_client_consent":
        if (!artifact.result?.client_consent) return null;
        return <LicenceCard result={artifact.result as any} />;
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
      case "read_messages":
      case "send_client_message":
        if (!Array.isArray(artifact.result?.threads) && !Array.isArray(artifact.result?.messages)) return null;
        return <MessagesCard result={artifact.result as any} />;
      case "read_provenance":
        if (typeof artifact.result?.ledger?.valid !== "boolean" || !Array.isArray(artifact.result?.surveillance?.flags) || !artifact.result?.surveillance?.by_severity || !Array.isArray(artifact.result?.evaluations) || !Array.isArray(artifact.result?.autonomy_changes)) return null;
        return <ProvenanceCard result={artifact.result as any} />;
      case "read_agents":
        if (!Array.isArray(artifact.result?.agents)) return null;
        return <AgentsCard result={artifact.result as any} />;
      case "read_tax_book":
        return isTaxBook(artifact.result) ? <TaxBookCard result={artifact.result as any} /> : null;
      case "read_client_canvas":
      case "read_my_wealth":
        return isClientCanvas(artifact.result) ? <ClientCanvasCard result={artifact.result as any} /> : null;
      case "read_my_net_worth":
        return isMyNetWorth(artifact.result) ? <MyNetWorthCard result={artifact.result as any} /> : null;
      case "read_my_ips":
        return isMyIps(artifact.result) ? <MyIpsCard result={artifact.result as any} /> : null;
      case "read_my_retirement":
        return isMyRetirement(artifact.result) ? <MyRetirementCard result={artifact.result as any} /> : null;
      case "read_my_goals":
        return isMyGoals(artifact.result) ? <MyGoalsCard result={artifact.result as any} /> : null;
      case "read_my_fees":
        return isMyFees(artifact.result) ? <MyFeesCard result={artifact.result as any} /> : null;
      case "read_my_report":
        return isMyReport(artifact.result) ? <MyReportCard result={artifact.result as any} /> : null;
      case "record_questionnaire":
        return isQuestionnaireRecorded(artifact.result) ? <QuestionnaireRecordedCard result={artifact.result as any} /> : null;
      case "read_estate":
        return isEstate(artifact.result) ? <EstateCard result={artifact.result as any} /> : null;
      case "read_family":
        return isFamily(artifact.result) ? <FamilyCard result={artifact.result as any} /> : null;
      case "read_behavioural":
        return isBehavioural(artifact.result) ? <BehaviouralCard result={artifact.result as any} /> : null;
      case "read_regulatory_countdown":
        return isRegulatoryCountdown(artifact.result) ? <RegulatoryCountdownCard result={artifact.result as any} /> : null;
      case "read_skills":
      case "create_skill":
      case "update_skill":
      case "clone_skill":
        return isSkills(artifact.result) ? <SkillsCard result={artifact.result as any} /> : null;
      case "read_book_batches":
      case "start_book_batch":
        return isBookBatch(artifact.result) ? <BookBatchCard result={artifact.result as any} /> : null;
      case "screen_book":
        if (!Array.isArray(artifact.result?.households) || typeof artifact.result?.households_screened !== "number"
          || !artifact.result.households.every((h: any) => Array.isArray(h?.equity) && h?.values && Array.isArray(h?.next_gen))) return null;
        return <BookScreenCard result={artifact.result as any} />;
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
