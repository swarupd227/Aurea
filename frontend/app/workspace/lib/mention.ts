/**
 * @-mentions in the composer. Pure: no React, no DOM.
 *
 * A mention travels as plain text ("@drift_rebalancing"); the backend only
 * needs the tool call it triggers, so nothing else has to parse it.
 */
import type { Mentionable } from "../types";

export interface MentionQuery {
  /** Index of the "@". */
  start: number;
  /** What's typed after the "@", up to the caret. */
  query: string;
}

const MAX_QUERY = 40;

/** The mention being typed at the caret, if any: an "@" at the start or after whitespace, with no line break since. */
export function findMentionQuery(text: string, caret: number): MentionQuery | null {
  const before = text.slice(0, caret);
  const at = before.lastIndexOf("@");
  if (at < 0) return null;
  if (at > 0 && !/\s/.test(before[at - 1])) return null; // an email address, not a mention
  const query = before.slice(at + 1);
  if (query.length > MAX_QUERY || /[\n\r]/.test(query) || query.startsWith(" ")) return null;
  return { start: at, query };
}

/** Replace the typed "@query" with "@name " and put the caret after it. */
export function applyMention(text: string, mention: MentionQuery, caret: number, name: string): { text: string; caret: number } {
  const insert = `@${name} `;
  const after = text.slice(caret).replace(/^ /, "");
  return { text: text.slice(0, mention.start) + insert + after, caret: mention.start + insert.length };
}

/** Agents matching the query: names starting with it, then a word starting with it, then containing it. */
export function rankMentionables(agents: Mentionable[], query: string, limit = 8): Mentionable[] {
  const q = query.trim().toLowerCase();
  if (!q) return agents.slice(0, limit);
  const score = (a: Mentionable) => {
    const name = a.name.toLowerCase();
    if (name.startsWith(q)) return 0;
    if (name.split(/[\s\-_/]+/).some((w) => w.startsWith(q))) return 1;
    return name.includes(q) ? 2 : -1;
  };
  return agents
    .map((a) => ({ a, s: score(a) }))
    .filter((x) => x.s >= 0)
    .sort((x, y) => x.s - y.s || x.a.name.localeCompare(y.a.name))
    .slice(0, limit)
    .map((x) => x.a);
}

/**
 * A mention that is already finished: the full name of an agent followed by a
 * space. The menu stays closed for it, so Enter sends the message instead of
 * reopening the same choice.
 */
export function isCompletedMention(query: string, agents: Mentionable[]): boolean {
  if (!/\s$/.test(query)) return false;
  const name = query.trim().toLowerCase();
  return !!name && agents.some((a) => a.name.toLowerCase() === name);
}

/** Names shared by more than one agent, which the menu disambiguates with the id. */
export function duplicateNames(agents: Mentionable[]): Set<string> {
  const seen = new Map<string, number>();
  for (const a of agents) seen.set(a.name.toLowerCase(), (seen.get(a.name.toLowerCase()) ?? 0) + 1);
  return new Set(Array.from(seen).filter(([, n]) => n > 1).map(([name]) => name));
}
