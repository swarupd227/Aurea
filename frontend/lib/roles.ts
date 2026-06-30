// Default landing route per persona (used by login + home redirect).
export const ROLE_LANDING: Record<string, string> = {
  superadmin: "/superadmin",
  admin: "/admin",
  adviser: "/studio",
  paraplanner: "/studio/meetings",
  portfolio_team: "/studio/review",
  research_cio: "/studio/reports",
  compliance: "/provenance",
  operations: "/studio/onboarding",
  branch_leader: "/studio/capacity",
  client: "/canvas",
};

export function roleLanding(role: string): string {
  return ROLE_LANDING[role] || "/studio";
}
