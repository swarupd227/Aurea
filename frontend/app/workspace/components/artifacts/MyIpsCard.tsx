import { titleCase } from "@/lib/format";
import { share } from "./IpsCard";
import { reviewDay } from "./ReviewsCard";
import { CardHead, CardShell, Chip, obj } from "./CanvasCardParts";

// The investment policy terms a client accepted (`read_my_ips`): the terms only - what the monitor
// finds goes to the adviser. A mandate with none recorded here is "not on file here", not "no IPS".

export interface MyIpsPolicy {
  mandate: string;
  version: number;
  accepted_on: string;
  targets: Record<string, number>;
  drift_band: number;
  max_position: number | null;
  min_cash: number | null;
  restricted: string[];
}

export interface MyIpsPayload {
  household_id: string;
  policies: MyIpsPolicy[];
  without: string[];
  message?: string;
}

const isPolicy = (p: any) => obj(p) && typeof p.mandate === "string" && obj(p.targets) && Array.isArray(p.restricted);

export function isMyIps(r: any): boolean {
  return obj(r) && Array.isArray(r.policies) && Array.isArray(r.without) && r.policies.every(isPolicy);
}

export function MyIpsCard({ result }: { result: MyIpsPayload }) {
  const r = result;
  return (
    <CardShell testid="my-ips-card">
      <CardHead title="Investment policy">
        <Chip>{r.policies.length} accepted</Chip>
        {r.without.length > 0 && <Chip tone="text-warn">{r.without.length} not on file here</Chip>}
      </CardHead>
      {r.policies.length > 0 && (
        <ul className="divide-y divide-border-soft" data-testid="my-ips-policies">
          {r.policies.map((p) => (
            <li key={`${p.mandate}-${p.version}`} className="px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="min-w-0 truncate text-sm text-ink">{p.mandate}</span>
                <span className="shrink-0 font-mono text-[11px] text-ink-muted">v{p.version} · {reviewDay(p.accepted_on)}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {Object.entries(p.targets).map(([cls, w]) => <Chip key={cls}>{titleCase(cls)} {share(w, 0)}</Chip>)}
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                <Chip>drift ±{share(p.drift_band, 0)}</Chip>
                <Chip>max position {p.max_position != null ? share(p.max_position, 0) : "none set"}</Chip>
                <Chip>min cash {p.min_cash != null ? share(p.min_cash, 0) : "none set"}</Chip>
                {p.restricted.length > 0 && <Chip tone="text-warn">restricted {p.restricted.join(", ")}</Chip>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {r.without.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-t border-border-soft px-3 py-1.5" data-testid="my-ips-without">
          {r.without.map((m) => <Chip key={m} tone="text-warn">{m} · not on file here</Chip>)}
        </div>
      )}
    </CardShell>
  );
}
