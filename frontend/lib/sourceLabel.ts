// What an answer read, in a person's words: "Balance sheet", not "read_balance_sheet".
const ACRONYMS: Record<string, string> = { ips: "IPS", crm: "CRM", wsp: "WSP", ai: "AI", pep: "PEP", aml: "AML", kyc: "KYC" };

export function sourceLabel(toolKey: string): string {
  const words = toolKey.replace(/^read_/, "").split("_").map((w) => ACRONYMS[w] ?? w);
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
