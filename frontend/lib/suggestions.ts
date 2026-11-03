// Suggestions are the next sentence the user would type, so each one is a complete request that
// an existing tool can answer. Nothing here states a fact about a client: names come from the
// households endpoint at the moment they are shown, and a sentence with no name to put in it falls
// back to a general one.
"use client";
import { useEffect, useState } from "react";
import { api, getUser } from "./api";
import { firmMarket } from "./format";
import { withRef } from "./refs";

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
    // A client reads no list of households; their chips name nobody.
    if (getUser()?.role === "client") return;
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
const REPLIES: Suggestion = { label: "Clients waiting on me", prompt: "Which clients are waiting for a reply from me?" };
const MEETINGS: Suggestion = { label: "This week's meetings", prompt: "What meetings do I have this week, and what do I need to prepare?" };
const RUNS: Suggestion = { label: "Recent agent runs", prompt: "Show me the most recent agent runs and whether any failed." };
const CAPACITY: Suggestion = { label: "Capacity", prompt: "How much capacity have the agents reclaimed, and what is still open?" };
const ANALYTICS: Suggestion = { label: "Headline analytics", prompt: "Give me the firm's headline analytics." };
const ONBOARDING: Suggestion = { label: "Onboarding cases", prompt: "Which onboarding cases are blocked or past their SLA?" };
const PROVENANCE: Suggestion = { label: "Ledger and flags", prompt: "Is the decision ledger intact, and are any surveillance flags open?" };

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
  // India has no wash-sale rule. What an adviser asks there is where each taxpayer stands.
  if (firmMarket() === "IN") {
    return h
      ? { label: `${short(h)} tax position`, prompt: `What is ${the(h)} tax position this tax year, taxpayer by taxpayer?` }
      : { label: "Tax position", prompt: "What is a household's tax position this tax year, taxpayer by taxpayer?" };
  }
  return h
    ? { label: `${short(h)} wash sales`, prompt: `Which lots could ${the(h)} household not harvest a loss on right now because of a wash sale?` }
    : { label: "Check wash sales", prompt: "Check a household's wash-sale calendar before a loss harvest." };
}

/** What to try on the workspace home, by role. Only sentences a tool the role holds can answer. */
export function roleStarters(role: string, ctx: SuggestionContext): Suggestion[] {
  const [h1, h2] = ctx.households;
  switch (role) {
    case "adviser":
      if (firmMarket() === "IN") return [WAITING, { label: "Waiting on clients", prompt: "Which proposals are waiting for a client's consent?" }, washSale(h1), perf(h1)];
      return [WAITING, REPLIES, MEETINGS, perf(h1)];
    case "paraplanner":
      return [view(h1), { label: "Beneficiary gaps", prompt: "Which accounts are missing beneficiaries?" }, MEETINGS, WAITING];
    case "portfolio_team":
      return [WAITING, { label: "Composites", prompt: "How are the composites performing?" }, ANALYTICS, AGENTS];
    case "research_cio":
      return [{ label: "Composites", prompt: "How are the composites performing?" }, ANALYTICS, { label: "Firm definition", prompt: "Does the firm definition check come back clean?" }, perf(h1), WAITING];
    case "compliance":
      return [PROVENANCE, { label: "Compliance program", prompt: "Show me the compliance program and any open conflicts." }, { label: "AI governance", prompt: "Summarise AI governance: what is overdue for review?" }, ONBOARDING, WAITING];
    case "operations":
      return [
        { label: "Prepare billing run", prompt: "Prepare this quarter's billing run." },
        { label: "Billing exceptions", prompt: "What's the billing status, and which bills are held or unpaid?" },
        { label: "Corporate actions", prompt: "Which corporate actions still need processing?" },
        { label: "Price freshness", prompt: "How fresh are my prices, and which ones can't be trusted?" },
        ONBOARDING,
        WAITING,
      ];
    case "branch_leader":
      return [
        CAPACITY,
        { label: "Billing status", prompt: "Is a billing run waiting for approval, and what are the exceptions?" },
        { label: "Prepare billing run", prompt: "Prepare this quarter's billing run." },
        PROVENANCE,
        AGENTS,
        WAITING,
      ];
    case "admin":
      return [{ label: "AI governance", prompt: "Summarise AI governance: what is overdue for review?" }, { label: "Billing status", prompt: "What's the billing status?" }];
    default:
      return [WAITING];
  }
}

// A client's own questions, each one their read-only tools answer (CLIENT_TOOLS). No client facts in them.
const MY_TRACK: Suggestion = { label: "Am I on track?", prompt: "Am I on track?" };
const MY_RETIREMENT: Suggestion = { label: "Will I be okay in retirement?", prompt: "Will I be okay in retirement?" };
const MY_WORTH: Suggestion = { label: "What do I own and owe?", prompt: "What do I own and owe?" };
const MY_IPS: Suggestion = { label: "What does my investment policy say?", prompt: "What does my investment policy say?" };
const MY_FEES: Suggestion = { label: "What am I paying in fees?", prompt: "What am I paying in fees?" };
const MY_GOALS: Suggestion = { label: "How do my goals look side by side?", prompt: "How do my goals look side by side?" };

function clientSuggestions(pathname: string): Suggestion[] {
  const route = pathname.replace(/\/$/, "");
  if (route.startsWith("/canvas/retirement")) return [MY_RETIREMENT, MY_TRACK, MY_WORTH];
  if (route.startsWith("/canvas/goals")) return [MY_GOALS, MY_TRACK, MY_RETIREMENT];
  return [MY_TRACK, MY_WORTH, MY_IPS, MY_FEES];
}

/** Chips for the conversation docked on a page, by route. Empty means "ask anything" with no chips. */
export function pageSuggestions(pathname: string, role: string, ctx: SuggestionContext): Suggestion[] {
  if (role === "client") return clientSuggestions(pathname);
  const [h1, h2] = ctx.households;
  const household = pathname.match(/^\/studio\/clients\/([0-9a-f-]{36})/i)?.[1];
  if (household) {
    return [
      { label: "Walk me through it", prompt: withRef("Show me the accounts, goals and holdings for this household.", "household", household) },
      { label: "Performance", prompt: withRef("How has this household performed against its benchmark?", "household", household) },
      { label: "Wash sales", prompt: withRef("Check this household for wash-sale conflicts.", "household", household) },
      { label: "Billing", prompt: withRef("Why was this household billed what it was?", "household", household) },
      { label: "Next review", prompt: withRef("When was this household last reviewed, and when is the next review due?", "household", household) },
      { label: "Against its IPS", prompt: withRef("How does this household's portfolio sit against its IPS?", "household", household) },
      { label: "Trades", prompt: withRef("What has been traded for this household in the last month, and what has settled?", "household", household) },
      { label: "Tax documents", prompt: withRef("Which tax documents are expected for this household, and what has arrived?", "household", household) },
      { label: "Cash movements", prompt: withRef("Are any payments waiting for this household, and how much of their required distribution is left this year?", "household", household) },
      { label: "Client file", prompt: withRef("What is on file for this household: trusted contact, estate documents, recent life events, and is anything out of date?", "household", household) },
      { label: "Financial plan", prompt: withRef("Will this household's money last, how much cash do they have in reserve, and what do their debts cost?", "household", household) },
      { label: "Concentration", prompt: withRef("Does this household have a concentrated position or equity grants, and what is the plan?", "household", household) },
      { label: "Leaving or estate", prompt: withRef("Is a departure or an estate open for this household, and what is left to do?", "household", household) },
      { label: "Net worth", prompt: withRef("What is this household's net worth, and what is on file for it?", "household", household) },
    ];
  }
  const route = pathname.replace(/\/$/, "");
  switch (route) {
    case "/studio":
      return [WAITING, { label: "Scan the book", prompt: "Have the next-best-action agent scan the book for opportunities." }, AGENTS, perf(h1)];
    case "/studio/workforce":
      return [AGENTS, RUNS, WAITING];
    case "/studio/agent-history":
      return [RUNS, { label: "Failed runs", prompt: "Did any agent run fail recently?" }, AGENTS];
    case "/studio/capacity":
      return [CAPACITY, RUNS, WAITING];
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
    case "/studio/prices":
      return [
        { label: "How fresh are my prices", prompt: "How fresh are my prices, and which ones can't be trusted?" },
        { label: "Fetch prices now", prompt: "Fetch prices now." },
        { label: "Failed symbols", prompt: "Did the last price run fail for any symbol, and why?" },
      ];
    case "/studio/licences":
      return [
        { label: "Waiting on clients", prompt: "Which proposals are waiting for a client's consent?" },
        { label: "What may agents do", prompt: "What can the agents do for an Investment Adviser's client, and for a discretionary Portfolio Manager's?" },
      ];
    case "/studio/protection":
      return [
        { label: "Largest gaps", prompt: "Which households have the largest protection gaps?" },
        { label: "Not assessed", prompt: "Who can't be assessed for protection, and what's missing?" },
        h1 ? { label: `${short(h1)}'s cover`, prompt: `What happens to ${the(h1)} if an earner dies or can't work?` } : { label: "Explain the basis", prompt: "How is a protection gap worked out, and what does it leave out?" },
        { label: "Who may need care", prompt: "Who is old enough for long-term care to be the question, and what would it cost them?" },
      ];
    case "/studio/risk-profiles":
      return [
        { label: "Too much risk", prompt: "Which mandates take more risk than the client's answers support?" },
        { label: "Not taken", prompt: "Which households haven't completed a risk questionnaire?" },
        h1 ? { label: `${short(h1)}'s profile`, prompt: `What does ${the(h1)} risk questionnaire indicate, and how does it compare with what's on file?` } : { label: "Explain the score", prompt: "How is a risk profile worked out from the questionnaire?" },
      ];
    case "/studio/reviews":
      return [
        { label: "Overdue reviews", prompt: "Which client reviews are overdue, and what have those clients paid since?" },
        { label: "No record", prompt: "Which households have no review on record?" },
        h1 ? { label: `${short(h1)}'s review`, prompt: `When was ${the(h1)} last reviewed, and what did it cover?` } : { label: "Due soon", prompt: "Which client reviews fall due in the next month?" },
      ];
    case "/studio/equity-comp":
      return [
        { label: "No plan", prompt: "Which concentrated positions have no plan to reduce them?" },
        { label: "Vesting soon", prompt: "Which grants vest in the next twelve months, and what are they worth?" },
        { label: "Plans behind", prompt: "Which concentration plans have passed their target date?" },
      ];
    case "/studio/products":
      return [
        { label: "Closed but held", prompt: "Which closed products do clients still hold, and how much?" },
        { label: "Reviews due", prompt: "Which product reviews are overdue?" },
        { label: "Dearer classes", prompt: "Where are clients in a dearer share class than the shelf offers?" },
      ];
    case "/studio/exits":
      return [
        { label: "What is open", prompt: "Which clients are leaving, which estates are open, and what is still to do on each?" },
        { label: "What stayed", prompt: "How much has stayed at the firm and how much has left through departures and estates?" },
        { label: "Blocked", prompt: "Which estates have no executor on file, and which accounts have no beneficiary?" },
      ];
    case "/studio/net-worth":
      return [
        { label: "Debts on file", prompt: "Which households have debts on file, and how much do they owe?" },
        { label: "Assets only", prompt: "Which households have no debts recorded, so their net worth is assets only?" },
        h1 ? { label: `${short(h1)}'s net worth`, prompt: `What is ${the(h1)}'s net worth, and what is it made of?` } : { label: "Cash flow", prompt: "Which households have a cash flow we can assess?" },
      ];
    case "/studio/ips":
      return [
        { label: "In breach", prompt: "Which mandates are outside their IPS, and by how much?" },
        { label: "No IPS on file", prompt: "Which mandates have no IPS on file, and how do they sit against their model?" },
        { label: "Check the book", prompt: "Run the IPS monitor over the book." },
      ];
    case "/studio/reconciliation":
      return [
        { label: "Do we reconcile", prompt: "Do we reconcile against the custodians? Which accounts have breaks or no statement?" },
        { label: "Open breaks", prompt: "What reconciliation breaks are open, and what is the likely cause of each?" },
        { label: "No statement", prompt: "Which accounts have no custodian statement on file?" },
        { label: "Work the queue", prompt: "Run the reconciliation agent over the open breaks." },
      ];
    case "/studio/valuations":
      return [{ label: "Private valuations", prompt: "What are the private holdings carried at, and which are overdue?" }, { label: "Price freshness", prompt: "How fresh are my prices?" }];
    case "/studio/clients":
      return [view(h1), perf(h1), washSale(h2 ?? h1)];
    case "/studio/analytics":
      return [ANALYTICS, { label: "Composites", prompt: "How are the composites performing?" }, { label: "Firm definition", prompt: "Does the firm definition check come back clean?" }];
    case "/studio/composites":
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
      return [PROVENANCE, RUNS, { label: "Compliance program", prompt: "Show me the compliance program and any open conflicts." }, { label: "AI governance", prompt: "Summarise AI governance: what is overdue for review?" }];
    case "/admin/regulatory":
      return [{ label: "Compliance program", prompt: "Show me the compliance program and any open conflicts." }, { label: "AI governance", prompt: "Summarise AI governance: what is overdue for review?" }];
    case "/studio/onboarding":
      return [ONBOARDING, WAITING, { label: "Beneficiary gaps", prompt: "Which accounts are missing beneficiaries?" }];
    case "/studio/book-integration":
      return [WAITING, { label: "Beneficiary gaps", prompt: "Which accounts are missing beneficiaries?" }];
    default:
      return role === "admin" ? [] : [WAITING];
  }
}
