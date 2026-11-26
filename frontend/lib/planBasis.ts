import { money, pct } from "@/lib/format";

// What a plan or a goal's figures rest on, as chip text, so every surface says it the same way.

/** Whose capital market assumptions the returns are (B-167). */
export const returnsLabel = (source?: string | null) =>
  source === "firm" ? "firm's return assumptions" : source === "market" ? "the market's suggested returns" : "platform's return assumptions";

/** A market's suggested planning figure (India row 5), as chip text: its label and value. The source is the chip's title. */
export function basisLabel(b: { key: string; label: string; value: number; assumed?: boolean }): string {
  const text = /return|inflation/.test(b.key) ? `${b.label} ${(b.value * 100).toFixed(1)}%` : `${b.label} ${b.value}`;
  return b.assumed ? `${text} · assumed` : text;
}

/** Where a retirement plan ends: the younger adult's plan-to age for a couple, and whose ages it counts (B-173). */
export function planToLabel(p: any): string {
  const to = p?.plan_to;
  return to?.whose === "younger_adult" && to.person ? `to ${to.person}'s ${p.longevity_age}` : `to age ${p?.longevity_age}`;
}

/** The ages a couple's plan is counted in, when they are one person's and the plan ends on another's. */
export const agesOfLabel = (p: any): string | null => (p?.plan_to?.whose === "younger_adult" && p?.ages_of ? `ages are ${p.ages_of}'s` : null);

/** A goal whose target is in today's money and grows with its own index to its date (B-172). */
export function targetBasisLabel(g: any): string | null {
  if (g?.target_basis !== "today" || g.target_today == null) return null;
  return `${money(g.target_today)} today · ${g.index ?? "cpi"} +${pct(g.inflation ?? 0, 1)}/yr`;
}

/** An inflation rate the firm has not set, standing in at the platform's default. */
export const RATE_ASSUMED: Record<string, string> = {
  cpi_inflation: "assumed CPI inflation",
  education_inflation: "assumed education inflation",
  healthcare_inflation: "assumed healthcare inflation",
};
