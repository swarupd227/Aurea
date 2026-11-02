// The names on file closest to a search that matched nothing, so a typo gets an offer and not a dead
// end. Only ever offered: a similar name can be a different client. The conversation's version is
// `aurea_core/client_names.py`; this one has only the household names a list already holds.

const NEAR = 0.75;
const NOISE = new Set(["the", "family", "household", "and", "of"]);

const words = (text: string) =>
  text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((w) => w.length > 1 && !NOISE.has(w));

/** Edits between two words, a swap of neighbours counting as one: "patle" is one slip from "patel". */
function edits(a: string, b: string): number {
  const row = (n: number) => Array.from({ length: n }, () => 0);
  const d = Array.from({ length: a.length + 1 }, () => row(b.length + 1));
  for (let i = 0; i <= a.length; i++) d[i][0] = i;
  for (let j = 0; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
    }
  }
  return d[a.length][b.length];
}

function likeness(query: string, name: string): number {
  let best = 0;
  for (const a of words(query)) {
    for (const b of words(name)) best = Math.max(best, 1 - edits(a, b) / Math.max(a.length, b.length));
  }
  return best;
}

export function closestNames<T extends { name: string }>(query: string, rows: T[], limit = 3): T[] {
  return rows
    .map((row) => ({ row, score: likeness(query, row.name) }))
    .filter((r) => r.score >= NEAR)
    .sort((a, b) => b.score - a.score || a.row.name.localeCompare(b.row.name))
    .slice(0, limit)
    .map((r) => r.row);
}
