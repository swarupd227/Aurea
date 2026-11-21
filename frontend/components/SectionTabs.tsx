"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { CLIENT_SECTIONS, CLIENT_ONLY, TAKES_HOUSEHOLD, sectionMembers, sectionOf, viewsForRole } from "@/lib/nav";

/** The tabs on a full page (the classic shell): links, read from the page's own URL. */
export function PageSectionTabs({ role }: { role: string }) {
  const pathname = usePathname();
  const search = useSearchParams();
  const q = search.toString();
  return (
    <div className="mb-4 empty:hidden">
      <SectionTabs route={q ? `${pathname}?${q}` : pathname} role={role} />
    </div>
  );
}

/** The tabs shared by the pages of one section (lib/nav.ts SECTIONS), or, on a page about one client, by that
 * client's sections. The menu lists a section once; these tabs move between its pages in one click, so the
 * pages stay where they were and the menu stays short.
 *
 * `route` is the open route with its query. `open` moves inside the work pane; without it the tabs are links. */
export function SectionTabs({ route, role, open }: { route: string; role: string; open?: (href: string) => void }) {
  const [path, query = ""] = route.split("?");
  const onClient = path.match(/^\/studio\/clients\/([^/]+)$/)?.[1];
  const household = onClient ?? new URLSearchParams(query).get("household") ?? new URLSearchParams(query).get("household_id");

  let tabs: { href: string; label: string }[] = [];
  let label = "";
  if (household && (onClient || TAKES_HOUSEHOLD.has(path) || CLIENT_ONLY.has(path))) {
    // One client: its overview first, then its sections, each still about this client.
    const mine = new Set(viewsForRole(role).map((v) => v.href));
    tabs = [
      { href: `/studio/clients/${household}`, label: "Overview" },
      ...CLIENT_SECTIONS.filter((s) => mine.has(s.href)).map((s) => ({ href: `${s.href}?household=${household}`, label: s.label })),
    ];
    label = "This client";
  } else {
    const section = sectionOf(path);
    if (!section) return null;
    tabs = sectionMembers(section, role).map((v) => ({ href: v.href, label: v.label }));
    label = section.label;
  }
  if (tabs.length < 2) return null;

  const current = (href: string) => href.split("?")[0] === path;
  const tabClass = (on: boolean) =>
    `shrink-0 whitespace-nowrap rounded-md px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
      on ? "bg-border-soft font-medium text-ink" : "text-ink-muted hover:bg-border-soft hover:text-ink"
    }`;

  return (
    // The row scrolls sideways when the pane is narrow; the fade at its right edge says there is more.
    <nav aria-label={label} data-testid="section-tabs"
      className="flex min-w-0 items-center gap-1 overflow-x-auto pr-6 [scrollbar-width:none] [mask-image:linear-gradient(to_right,black_calc(100%-2rem),transparent)]">
      {tabs.map((t) =>
        open ? (
          <button key={t.href} type="button" onClick={() => !current(t.href) && open(t.href)} aria-current={current(t.href) ? "page" : undefined}
            className={tabClass(current(t.href))}>
            {t.label}
          </button>
        ) : (
          <Link key={t.href} href={t.href} aria-current={current(t.href) ? "page" : undefined} className={tabClass(current(t.href))}>
            {t.label}
          </Link>
        )
      )}
    </nav>
  );
}
