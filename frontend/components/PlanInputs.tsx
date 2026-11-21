import { formatDateFull, money, pct } from "@/lib/format";

// What a plan works from, as chips (B-182), and the return-order illustration beside the first-year crash (B-195).
// Figures and short labels only: the sentences are the agent's.

const FACT: Record<string, string> = {
  date_of_birth: "date of birth",
  annual_income: "income",
  retirement_goal: "retirement goal",
  retirement_age: "retirement age",
  plan_to_age: "plan-to age",
  funding_share: "funding share",
  income_need: "income to plan for",
  education_cost: "education cost",
};

const muted = "chip bg-border-soft text-ink-muted tabular-nums";
const warn = "chip bg-warn-bg text-warn tabular-nums";

/** How old the plan's inputs are, against the firm's line, and the facts it reads that are not on file. */
export function PlanInputsChips({ inputs, testid = "plan-inputs" }: { inputs: any; testid?: string }) {
  if (!inputs) return null;
  const c = inputs.completeness || {};
  return (
    <div className="flex flex-wrap gap-1.5" data-testid={testid}>
      <span className={muted} data-testid={`${testid}-oldest`}>
        {inputs.oldest ? `oldest input ${formatDateFull(inputs.oldest.as_of)} · ${inputs.oldest.days}d` : "no dated input"}
      </span>
      <span className={inputs.older_than_line > 0 ? warn : muted} data-testid={`${testid}-older`}>
        {inputs.older_than_line} of {inputs.inputs} older than {inputs.line_days}d
      </span>
      {inputs.line_assumed && <span className={muted}>{inputs.line_days}-day line assumed</span>}
      {inputs.undated > 0 && <span className={warn}>{inputs.undated} undated</span>}
      {c.read > 0 && (
        <span className={c.on_file < c.read ? warn : muted} data-testid={`${testid}-complete`}>
          {c.on_file}/{c.read} facts on file
        </span>
      )}
      {(inputs.missing || []).map((m: any, i: number) => (
        <span key={`${m.fact}-${i}`} className={warn} data-testid={`${testid}-missing`}>
          not on file: {FACT[m.fact] ?? m.fact}{m.person || m.goal ? ` · ${m.person ?? m.goal}` : ""}
        </span>
      ))}
    </div>
  );
}

/** The same returns run on the household's pot and draw, weakest years first and then reversed. */
export function SequenceIllustration({ ill, testid = "sequence-illustration" }: { ill: any; testid?: string }) {
  if (!ill?.orders?.length) return null;
  const label: Record<string, string> = { weak_first: "weak years first", strong_first: "reversed" };
  return (
    <div className="flex flex-wrap gap-1.5" data-testid={testid}>
      <span className={muted}>illustration · same returns, avg {pct(ill.average_return, 1)}/yr</span>
      {ill.orders.map((o: any) => (
        <span key={o.order} className={o.depletes_at != null ? warn : muted} data-testid={`${testid}-${o.order}`}>
          {label[o.order] ?? o.order}: {o.depletes_at != null ? `runs out at ${o.depletes_at}` : `${money(o.ending_balance_today)} left · today's money`}
        </span>
      ))}
    </div>
  );
}
