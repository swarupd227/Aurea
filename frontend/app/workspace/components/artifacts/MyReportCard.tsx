import { titleCase } from "@/lib/format";
import { CardHead, CardShell, Chip, obj } from "./CanvasCardParts";

// One report published to the client (`read_my_report`): its title, period and what it covers. The
// section bodies are the report's own prose and are not drawn on the card.

export interface MyReportPayload {
  id: string;
  household_id: string;
  title: string;
  period: string | null;
  sections: { heading?: string; body?: string }[];
  data?: Record<string, any>;
  status: string;
  message?: string;
}

export function isMyReport(r: any): boolean {
  return obj(r)
    && typeof r.id === "string"
    && typeof r.title === "string"
    && "period" in r
    && Array.isArray(r.sections)
    && typeof r.status === "string";
}

export function MyReportCard({ result }: { result: MyReportPayload }) {
  const r = result;
  const headings = r.sections.map((s) => (obj(s) && typeof s.heading === "string" ? s.heading : null)).filter(Boolean) as string[];
  return (
    <CardShell testid="my-report-card">
      <CardHead title={r.title}>
        {r.period && <Chip>{r.period}</Chip>}
        <Chip tone={r.status === "client_ready" ? "text-ok" : "text-ink-muted"}>{r.status === "client_ready" ? "published" : titleCase(r.status)}</Chip>
        <Chip>{r.sections.length} sections</Chip>
      </CardHead>
      {headings.length > 0 && (
        <ol className="divide-y divide-border-soft" data-testid="my-report-sections">
          {headings.map((h, i) => (
            <li key={`${i}-${h}`} className="flex items-center gap-2 px-3 py-1.5 text-sm">
              <span className="w-5 shrink-0 font-mono text-xs text-ink-muted">{i + 1}</span>
              <span className="min-w-0 truncate text-ink">{h}</span>
            </li>
          ))}
        </ol>
      )}
    </CardShell>
  );
}
