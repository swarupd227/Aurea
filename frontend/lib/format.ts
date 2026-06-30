export function money(n: number | null | undefined, ccy = "NZD"): string {
  if (n === null || n === undefined || isNaN(n as number)) return "—";
  return new Intl.NumberFormat("en-NZ", {
    style: "currency",
    currency: ccy,
    maximumFractionDigits: 0,
  }).format(n);
}

export function pct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || isNaN(n as number)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function num(n: number, digits = 0): string {
  return new Intl.NumberFormat("en-NZ", { maximumFractionDigits: digits }).format(n);
}

export function titleCase(s: string): string {
  return (s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso).getTime();
  const diff = Date.now() - d;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function formatDateFull(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-NZ", {
    day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export const ASSET_COLORS: Record<string, string> = {
  equity: "#1d4663",
  fixed_income: "#5d86a3",
  cash: "#c8a35e",
  alternatives: "#7c6a9c",
  property: "#3f8a72",
  commodity: "#b9852b",
  multi_asset: "#9aa7b2",
};
