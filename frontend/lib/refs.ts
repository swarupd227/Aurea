// A sentence a button or chip sends on someone's behalf names the client in words, and carries the
// exact record it means as a trailing marker - "[ref: household <id>]" - so the model uses that id
// rather than guessing from a name. People never need to read the marker: anywhere a user's sentence
// is shown or becomes a title, it goes through `readable`. The "Ask about this" prefix
// ("[Open view: …]") is the same kind of marker and is hidden the same way.

const MARKER = /\s*\[(?:ref|Open view):[^\]]*\]\s*/g;

export function withRef(sentence: string, kind: "household" | "mandate" | "recommendation" | "invoice" | "onboarding_case" | "meeting" | "task" | "report", id: string): string {
  return `${sentence} [ref: ${kind} ${id}]`;
}

export function readable(text: string): string {
  return text.replace(MARKER, " ").replace(/\s{2,}/g, " ").trim();
}
