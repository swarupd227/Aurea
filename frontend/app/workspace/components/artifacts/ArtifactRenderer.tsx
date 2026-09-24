import { PortfolioCard } from "./PortfolioCard";
import { OrdersCard } from "./OrdersCard";
import { CompliancePanelCard } from "./CompliancePanelCard";
import { CrmPipelineCard } from "./CrmPipelineCard";
import { CorporateActionsCard } from "./CorporateActionsCard";
import { SleevesCard } from "./SleevesCard";
import { SleeveNettingCard } from "./SleeveNettingCard";
import { WashSaleCalendarCard } from "./WashSaleCalendarCard";
import type { Artifact } from "../../types";

/**
 * One artifact per completed tool call. A tool_key without a card here renders
 * nothing — the prose answer already covers it, and a raw JSON dump would be
 * noise, not information. Wrapped defensively: a shape that doesn't match what
 * a card expects (e.g. an executor's stub/error path) is skipped rather than
 * crashing the message.
 */
export function ArtifactRenderer({ artifact }: { artifact: Artifact }) {
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
      default:
        return null;
    }
  } catch {
    return null;
  }
}
