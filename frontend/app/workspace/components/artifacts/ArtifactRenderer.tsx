import { PortfolioCard } from "./PortfolioCard";
import { OrdersCard } from "./OrdersCard";
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
      default:
        return null;
    }
  } catch {
    return null;
  }
}
