"use client";

import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import { useRouter } from "next/navigation";
import {
  BookOpen, Bot, Building, Cpu, Users, ClipboardList, UserSquare2, PlugZap, BarChart2, FileText,
  Database, Clock, Layers, Link2, Bell, Target, Paintbrush, Search,
} from "lucide-react";
import { getUser } from "@/lib/api";
import { useViewNav } from "./ViewNav";
import { ViewHeader, chip } from "./ViewHeader";
import { FirmTab, BrandingTab } from "./admin/FirmTabs";
import { UsersTab, ClientsTab, AssignmentsTab } from "./admin/PeopleTabs";
import { SegmentsTab, MandatesTab, ModelPortfoliosTab } from "./admin/ProductTabs";
import { ConnectorsTab, SchedulesTab, NotificationsTab, DataQualityTab } from "./admin/OpsTabs";
import { RuleImpactTab, AuditTab } from "./admin/ComplianceTabs";
import { AgentsTab, ResearchTab, ModelsTab, UsageTab } from "./admin/IntelligenceTabs";

export const ADMIN_GROUPS = [
  {
    label: "Firm",
    items: [
      { id: "firm",     label: "Firm & branding", icon: Building },
      { id: "branding", label: "Branding",        icon: Paintbrush },
    ],
  },
  {
    label: "People & access",
    items: [
      { id: "users",       label: "Users",       icon: Users },
      { id: "clients",     label: "Clients",     icon: UserSquare2 },
      { id: "assignments", label: "Assignments", icon: Link2 },
    ],
  },
  {
    label: "Product config",
    items: [
      { id: "segments",         label: "Segments",         icon: Layers },
      { id: "mandates",         label: "Mandates",         icon: FileText },
      { id: "model-portfolios", label: "Model portfolios", icon: Target },
    ],
  },
  {
    label: "Operations",
    items: [
      { id: "connectors",    label: "Connectors",    icon: PlugZap },
      { id: "schedules",     label: "Schedules",     icon: Clock },
      { id: "notifications", label: "Notifications", icon: Bell },
      { id: "data-quality",  label: "Data quality",  icon: Database },
    ],
  },
  {
    label: "Compliance",
    items: [
      { id: "rule-impact", label: "Rule impact", icon: Search },
      { id: "audit",       label: "Audit trail", icon: ClipboardList },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { id: "agents",   label: "Agents & autonomy", icon: Bot },
      { id: "research", label: "Firm research",     icon: BookOpen },
      { id: "models",   label: "Models",            icon: Cpu },
      { id: "usage",    label: "AI usage",          icon: BarChart2 },
    ],
  },
];

const TABS = ADMIN_GROUPS.flatMap((g) => g.items.map((i) => ({ ...i, group: g.label })));
const DEFAULT_TAB = "firm";

/** Who may read each tab, as its routes allow: "governance" is the admin and compliance (`api/admin.AdminDep`);
 * "staff" is any staff role (the agents catalogue, connectors and model portfolios). A role sees only the tabs it
 * can read, so none opens on a refusal (B-209). `tests.verify_studio_views` holds this to the routes. */
export const TAB_READERS: Record<string, "staff" | "governance"> = {
  firm: "governance", branding: "governance", users: "governance", clients: "governance", assignments: "governance",
  segments: "governance", mandates: "governance", "model-portfolios": "staff", connectors: "staff",
  schedules: "governance", notifications: "governance", "data-quality": "governance", "rule-impact": "governance",
  audit: "governance", agents: "staff", research: "governance", models: "governance", usage: "governance",
};
const GOVERNANCE_ROLES = ["admin", "compliance"];
// The tab a role that cannot read Firm opens on: its own work first.
const OPENS_ON: Record<string, string> = { operations: "connectors", portfolio_team: "model-portfolios", research_cio: "model-portfolios" };

const canRead = (id: string, role: string | undefined) =>
  TAB_READERS[id] === "staff" || GOVERNANCE_ROLES.includes(role ?? "");

const CONTENT: Record<string, ComponentType> = {
  firm: FirmTab,
  branding: BrandingTab,
  users: UsersTab,
  clients: ClientsTab,
  assignments: AssignmentsTab,
  segments: SegmentsTab,
  mandates: MandatesTab,
  "model-portfolios": ModelPortfoliosTab,
  connectors: ConnectorsTab,
  schedules: SchedulesTab,
  notifications: NotificationsTab,
  "data-quality": DataQualityTab,
  "rule-impact": RuleImpactTab,
  audit: AuditTab,
  agents: AgentsTab,
  research: ResearchTab,
  models: ModelsTab,
  usage: UsageTab,
};

/** The firm's configuration, tab by tab. Beside the conversation every change is a sentence the matching
 * admin tool answers behind its Confirm card; on the classic page the controls act directly. The tab is
 * the `?tab=` query, so a tab is linkable either way. */
export function AdminView({ tab = null }: { tab?: string | null }) {
  const nav = useViewNav();
  const router = useRouter();
  const role: string | undefined = getUser()?.role;
  const groups = ADMIN_GROUPS
    .map((g) => ({ ...g, items: g.items.filter((i) => canRead(i.id, role)) }))
    .filter((g) => g.items.length > 0);
  const visible = groups.flatMap((g) => g.items.map((i) => ({ ...i, group: g.label })));
  const hidden = TABS.length - visible.length;
  const opensOn = visible.find((t) => t.id === (OPENS_ON[role ?? ""] ?? DEFAULT_TAB))?.id ?? visible[0]?.id ?? DEFAULT_TAB;
  const wanted = tab && CONTENT[tab] && canRead(tab, role) ? tab : opensOn;
  const [active, setActive] = useState(wanted);
  useEffect(() => setActive(wanted), [wanted]);

  function go(id: string) {
    if (id === active) return;
    setActive(id);
    if (nav.inPane) nav.open(`/admin?tab=${id}`);
    else router.replace(`/admin?tab=${id}`, { scroll: false });
  }

  const current = visible.find((t) => t.id === active) || visible[0];
  const Content = CONTENT[current.id];

  return (
    <div className="view-container" data-testid="admin-view">
      <ViewHeader
        title={current.label}
        meta={
          <>
            <span className={chip}>{current.group}</span>
            {hidden > 0 && (
              <span className={chip} data-testid="admin-hidden-tabs" title="Firm settings the admin changes and compliance reads">
                {hidden} more · admin & compliance
              </span>
            )}
          </>
        }
      />
      <div className="grid gap-5 cq-lg:grid-cols-[12rem_minmax(0,1fr)]">
        <nav aria-label="Configuration" className="flex flex-wrap gap-x-5 gap-y-3 cq-lg:flex-col cq-lg:gap-4 cq-lg:border-r cq-lg:border-border-soft cq-lg:pr-3">
          {groups.map((group) => (
            <div key={group.label} className="min-w-0">
              <div className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-ink-faint">{group.label}</div>
              <div className="flex flex-wrap gap-1 cq-lg:flex-col cq-lg:gap-0.5" role="tablist">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const on = active === item.id;
                  return (
                    <button
                      key={item.id}
                      data-testid={`admin-tab-${item.id}`}
                      role="tab"
                      aria-selected={on}
                      onClick={() => go(item.id)}
                      className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors ${
                        on ? "bg-border-soft font-medium text-ink" : "text-ink-muted hover:bg-border-soft hover:text-ink"
                      }`}
                    >
                      <Icon size={14} className="shrink-0" />
                      <span className="truncate">{item.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
        <div className="min-w-0" role="tabpanel" data-testid={`admin-panel-${current.id}`}>
          <Content />
        </div>
      </div>
    </div>
  );
}
