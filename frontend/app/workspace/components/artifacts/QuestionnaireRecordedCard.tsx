import { titleCase } from "@/lib/format";
import { CardShell, Chip, obj } from "./CanvasCardParts";

// A risk questionnaire recorded for a client (`record_questionnaire`): what the answers indicate.
// The questionnaire indicates and an adviser decides, so no mandate has changed.

export interface QuestionnaireRecordedPayload {
  assessment_id: string;
  household_id: string;
  indicated: string | null;
  message?: string;
}

export function isQuestionnaireRecorded(r: any): boolean {
  return obj(r) && typeof r.assessment_id === "string" && typeof r.household_id === "string"
    && (typeof r.indicated === "string" || r.indicated === null);
}

export function QuestionnaireRecordedCard({ result }: { result: QuestionnaireRecordedPayload }) {
  return (
    <CardShell testid="questionnaire-recorded-card">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
        <div className="text-sm font-medium text-ink">Questionnaire recorded</div>
        <div className="flex flex-wrap gap-1.5">
          {result.indicated
            ? <Chip tone="text-ink" testid="questionnaire-indicated">indicates {titleCase(result.indicated)}</Chip>
            : <Chip tone="text-warn" testid="questionnaire-indicated">nothing indicated</Chip>}
          <Chip testid="questionnaire-no-mandate-change">no mandate changed</Chip>
        </div>
      </div>
    </CardShell>
  );
}
