export interface Starter {
  label: string;
  prompt: string;
}

export const STARTERS: Starter[] = [
  { label: "Review a household", prompt: "Show me a household's accounts, goals and holdings." },
  { label: "Check a portfolio", prompt: "What's the current allocation and performance for a portfolio?" },
  { label: "Decide on a recommendation", prompt: "What recommendations are waiting for a decision?" },
  { label: "Prepare for a rebalance", prompt: "What would a drift rebalance look like for a client?" },
];
