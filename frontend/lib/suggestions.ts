// Suggestions are the next sentence the user would type, so each one is a complete request that
// an existing tool can answer. Nothing here states a fact about a client: names come from the
// households endpoint at the moment they are shown, and a sentence with no name to put in it falls
// back to a general one.
"use client";
import { useEffect, useState } from "react";
import { api } from "./api";

export interface Suggestion {
  /** Short chip text. */
  label: string;
  /** The sentence sent to Astra. */
  prompt: string;
}

export interface SuggestionContext {
  /** Up to two household names, read from the API, used to make a sentence concrete. */
  households: string[];
}

let cached: string[] | null = null;

/** Household names for filling in suggestions. Fetched once per page load; empty on any failure. */
export function useSuggestionContext(): SuggestionContext {
  const [households, setHouseholds] = useState<string[]>(cached ?? []);
  useEffect(() => {
    if (cached) return;
    let live = true;
    api<any[]>("/api/core/households")
      .then((rows) => {
        cached = (rows || []).slice(0, 2).map((h) => String(h.name));
        if (live) setHouseholds(cached);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  return { households };
}

/** "the Chen Family", whether or not the stored name already begins with "The". */
const the = (h: string) => (/^the\s/i.test(h) ? `the${h.slice(3)}` : `the ${h}`);
/** The name as a chip label, without a leading "The". */
const short = (h: string) => h.replace(/^the\s+/i, "");

const WAITING: Suggestion = { label: "What's waiting on me", prompt: "What proposals are waiting for my decision?" };
const AGENTS: Suggestion = { label: "Which agents can I run", prompt: "Which agents can I run?" };

function perf(h?: string): Suggestion {
  return h
    ? { label: `${short(h)} performance`, prompt: `How has ${the(h)} portfolio performed against its benchmark?` }
    : { label: "Check performance", prompt: "How has a household's portfolio performed against its benchmark?" };
}

function view(h?: string): Suggestion {
  return h
    ? { label: `Open ${short(h)}`, prompt: `Show me ${the(h)} accounts, goals and holdings.` }
    : { label: "Review a household", prompt: "Show me a household's accounts, goals and holdings." };
}

function washSale(h?: string): Suggestion {
  return h
    ? { label: `${short(h)} wash sales`, prompt: `Which lots could ${the(h)} household not harvest a loss on right now because of a wash sale?` }
    : { label: "Check wash sales", prompt: "Check a household's wash-sale calendar before a loss harvest." };
}

/** What to try on the workspace home, by role. Only sentences a tool the role holds can answer. */
export function roleStarters(role: string, ctx: SuggestionContext): Suggestion[] {
  const [h1, h2] = ctx.households;
  switch (role) {
    case "adviser":
      return [WAITING, perf(h1), washSale(h2 ?? h1), AGENTS];
    case "paraplanner":
      return [view(h1), { label: "Beneficiary gaps", prompt: "Which accounts are missing beneficiaries?" }, AGENTS, WAITING];
    case "portfolio_team":
      return [WAITING, { label: "Composites", prompt: "How are the composites performing?" }, AGENTS, view(h1)];
    case "research_cio":
      return [{ label: "Composites", prompt: "How are the composites performing?" }, { label: "Firm definition", prompt: "Does the firm definition check come back clean?" }, perf(h1), WAITING];
    case "compliance":
      return [{ label: "Compliance program", prompt: "Show me the compliance program and any open conflicts." }, { label: "AI governance", prompt: "Summarise AI governance: what is overdue for review?" }, { label: "Beneficiary gaps", prompt: "Which accounts are missing beneficiaries?" }, WAITING];
    case "operations":
      return [
        { label: "Prepare billing run", prompt: "Prepare this quarter's billing run." },
        { label: "Billing exceptions", prompt: "What's the billing status, and which bills are held or unpaid?" },
        { label: "Corporate actions", prompt: "Which corporate actions still need processing?" },
        WAITING,
      ];
    case "branch_leader":
      return [
        { label: "Billing status", prompt: "Is a billing run waiting for approval, and what are the exceptions?" },
        { label: "Prepare billing run", prompt: "Prepare this quarter's billing run." },
        AGENTS,
        WAITING,
      ];
    case "admin":
      return [{ label: "AI governance", prompt: "Summarise AI governance: what is overdue for review?" }, { label: "Billing status", prompt: "What's the billing status?" }];
    default:
      return [WAITING];
  }
}

/** Chips for the conversation docked on a page, by route. Empty means "ask anything" with no chips. */
export function pageSuggestions(pathname: string, role: string, ctx: SuggestionContext): Suggestion[] {
  const [h1, h2] = ctx.households;
  const household = pathname.match(/^\/studio\/clients\/([0-9a-f-]{36})/i)?.[1];
  if (household) {
    return [
      { label: "Walk me through it", prompt: `Show me the accounts, goals and holdings for household ${household}.` },
      { label: "Performance", prompt: `How has household ${household} performed against its benchmark?` },
      { label: "Wash sales", prompt: `Check household ${household} for wash-sale conflicts.` },
      { label: "Billing", prompt: `Why was household ${household} billed what it was?` },
    ];
  }
  const route = pathname.replace(/\/$/, "");
  switch (route) {
    case "/studio":
      return [WAITING, { label: "Scan the book", prompt: "Have the next-best-action agent scan the book for opportunities." }, AGENTS, perf(h1)];
    case "/studio/workforce":
      return [AGENTS, WAITING];
    case "/studio/review":
      return [
        { label: "What's waiting on me", prompt: "What proposals are waiting for my decision, most urgent first?" },
        { label: "Rebalance proposals", prompt: "Which of the open proposals move money on a client's book?" },
        perf(h1),
      ];
    case "/studio/billing":
      return [
        { label: "Prepare billing run", prompt: "Prepare this quarter's billing run." },
        { label: "Held bills", prompt: "Which bills are held, and why?" },
        h1 ? { label: `Why ${short(h1)}'s bill`, prompt: `Why was ${the(h1)} billed what it was?` } : { label: "Explain a bill", prompt: "Explain a household's latest bill." },
      ];
    case "/studio/clients":
      return [view(h1), perf(h1), washSale(h2 ?? h1)];
    case "/studio/composites":
    case "/studio/analytics":
      return [{ label: "Composites", prompt: "How are the composites performing?" }, { label: "Firm definition", prompt: "Does the firm definition check come back clean?" }];
    case "/studio/pipeline":
      return [{ label: "Growth pipeline", prompt: "Show me the CRM growth pipeline." }];
    case "/studio/corporate-actions":
      return [{ label: "What needs processing", prompt: "Which corporate actions still need processing?" }];
    case "/studio/beneficiary-audit":
      return [{ label: "Beneficiary gaps", prompt: "Which accounts are missing beneficiaries?" }];
    case "/studio/tax":
    case "/studio/estate":
    case "/studio/family":
      return [view(h1), washSale(h1)];
    case "/provenance":
    case "/admin/regulatory":
      return [{ label: "Compliance program", prompt: "Show me the compliance program and any open conflicts." }, { label: "AI governance", prompt: "Summarise AI governance: what is overdue for review?" }];
    case "/studio/onboarding":
    case "/studio/book-integration":
      return [WAITING, { label: "Beneficiary gaps", prompt: "Which accounts are missing beneficiaries?" }];
    default:
      return role === "admin" ? [] : [WAITING];
  }
}
