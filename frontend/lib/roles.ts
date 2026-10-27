// Default landing route per persona (used by login + home redirect).
//
// Staff land in the conversation: the workspace is the home, pages are views the agents open.
// Configuration owners (admin, superadmin) and clients keep their page: their work is a form
// or the client's own view, not a conversation with the workforce.
export const WORKSPACE_HOME = "/workspace";

export const ROLE_LANDING: Record<string, string> = {
  superadmin: "/superadmin",
  admin: "/admin",
  adviser: WORKSPACE_HOME,
  paraplanner: WORKSPACE_HOME,
  portfolio_team: WORKSPACE_HOME,
  research_cio: WORKSPACE_HOME,
  compliance: WORKSPACE_HOME,
  operations: WORKSPACE_HOME,
  branch_leader: WORKSPACE_HOME,
  client: "/canvas",
};

export function roleLanding(role: string): string {
  return ROLE_LANDING[role] || WORKSPACE_HOME;
}
