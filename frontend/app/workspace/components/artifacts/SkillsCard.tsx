import { titleCase } from "@/lib/format";

// Skills, for `read_skills` (a list) and for create / update / clone (the one skill they touched).
// Figures and chips only - the agent's message carries the sentences.

const Chip = ({ children, tone = "text-ink-muted" }: { children: React.ReactNode; tone?: string }) => (
  <span className={`inline-block rounded border border-border px-1.5 py-0.5 font-mono text-[11px] ${tone}`}>{children}</span>
);

export interface SkillRow {
  id: string;
  name: string;
  description: string | null;
  instruction: string;
  scope: string;
  output_kind: string;
  default_tier: string;
  enabled: boolean;
  visibility: string;
  shared_with: string[];
  owner_id: string | null;
  owner_name: string;
  mine: boolean;
  can_edit: boolean;
  created_at: string | null;
}

export interface SkillsPayload {
  skills?: SkillRow[];
  skill?: SkillRow;
  message?: string;
}

const isSkillRow = (s: any) =>
  !!s && typeof s === "object" && typeof s.id === "string" && typeof s.name === "string"
  && typeof s.scope === "string" && typeof s.visibility === "string";

export function isSkills(r: any): boolean {
  if (!r || typeof r !== "object") return false;
  if (Array.isArray(r.skills)) return r.skills.every(isSkillRow);
  return isSkillRow(r.skill);
}

const VIS_TONE: Record<string, string> = { public: "text-ink-soft", shared: "text-ink-soft", private: "text-ink-muted" };

export function SkillsCard({ result }: { result: SkillsPayload }) {
  const rows = result.skills ?? (result.skill ? [result.skill] : []);
  const mine = rows.filter((s) => s.mine).length;
  const disabled = rows.filter((s) => !s.enabled).length;
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="skills-card">
      <div className="flex items-center justify-between border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">{result.skill && !result.skills ? "Skill" : "Skills"}</div>
        <div className="font-mono text-xs tabular-nums text-ink-muted">{rows.length}</div>
      </div>
      {result.skills && rows.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-border-soft px-3 py-1.5">
          <Chip>Yours {mine}</Chip>
          {disabled > 0 && <Chip tone="text-crit">Disabled {disabled}</Chip>}
        </div>
      )}
      <table className="w-full text-sm">
        <tbody className="divide-y divide-border-soft">
          {rows.map((s) => (
            <tr key={s.id}>
              <td className="px-3 py-1.5">
                <div className={s.enabled ? "text-ink" : "text-ink-muted line-through"}>{s.name}</div>
                <div className="text-xs text-ink-muted">{!s.owner_id ? "Firm library" : s.mine ? "Yours" : s.owner_name}</div>
              </td>
              <td className="px-3 py-1.5">
                <div className="flex flex-wrap justify-end gap-1">
                  <Chip tone={VIS_TONE[s.visibility]}>{titleCase(s.visibility)}{s.visibility === "shared" ? ` ${s.shared_with?.length ?? 0}` : ""}</Chip>
                  <Chip>{s.scope === "household" ? "Household" : "Book"}</Chip>
                  <Chip>{titleCase(s.output_kind)}</Chip>
                  <Chip>T{String(s.default_tier).replace("tier_", "")}</Chip>
                  {!s.enabled && <Chip tone="text-crit">Disabled</Chip>}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
