import { Wrench, DatabaseZap } from "lucide-react";
import type { Artifact } from "../types";

function pricingSummary(artifacts: Artifact[]): { text: string; allReal: boolean } | null {
  for (const a of artifacts) {
    const pricing = a.result?.pricing;
    if (pricing && (pricing.real || pricing.synthetic)) {
      const total = pricing.real + pricing.synthetic;
      if (pricing.synthetic === 0) return { text: `${total} holding(s), real market prices`, allReal: true };
      if (pricing.real === 0) return { text: `${total} holding(s), synthetic prices (demo data)`, allReal: false };
      return { text: `${pricing.real} real, ${pricing.synthetic} synthetic price(s)`, allReal: false };
    }
  }
  return null;
}

/**
 * What this answer is actually based on — genuine signals this system tracks,
 * not a fixed three-category template. Tools always shown when any ran; pricing
 * provenance only appears when an artifact actually carries it (a portfolio or
 * order result), per the platform's rule that a synthetic-priced valuation says
 * so rather than passing as real.
 */
export function ProofStrip({ tools, artifacts }: { tools: string[]; artifacts: Artifact[] }) {
  if (tools.length === 0) return null;
  const pricing = pricingSummary(artifacts);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border-soft pt-2 font-mono text-[11px]">
      <span
        className="inline-flex items-center gap-1.5 text-ink-muted"
        title={`Tool call(s) this answer used: ${tools.join(", ")}`}
      >
        <Wrench className="h-3 w-3 shrink-0 text-ok" aria-hidden />
        {tools.map((t) => t.replace(/_/g, " ")).join(" · ")}
      </span>
      {pricing && (
        <span
          className={`inline-flex items-center gap-1.5 ${pricing.allReal ? "text-ink-muted" : "text-warn"}`}
          title={
            pricing.allReal
              ? "Every holding priced from the real market feed."
              : "At least one holding used a synthetic price — this environment's demo data, not a live quote. Never presented as a real market price."
          }
        >
          <DatabaseZap className="h-3 w-3 shrink-0" aria-hidden />
          {pricing.text}
        </span>
      )}
    </div>
  );
}
