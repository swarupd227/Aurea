"use client";

import { createContext, useCallback, useContext, useEffect, useMemo } from "react";
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { matchPaneView } from "@/lib/paneViews";

/**
 * How a view moves and acts, wherever it is rendered. The same view component is a classic page
 * (/studio/clients) and a work-pane view beside the conversation (/workspace?view=/studio/clients);
 * it never asks which. It asks this:
 *
 *  - `open(route)` - go to another view. In the pane a pane view replaces the pane's content (with
 *    a breadcrumb back); on a page, or for a route that is not a pane view, it is a normal link.
 *  - `href(route)` - the URL that same move lands on, for real links (middle-click, copy link).
 *  - `say(text)` - hand a sentence to the conversation beside the view. Only in the pane: there an
 *    agent run is the conversation's `run_agent`, behind its Confirm card, not a side channel.
 *    Null on a classic page, where a view keeps its own controls.
 *  - `changed()` - the view changed something (a decision): the thread and the rail look again.
 *  - `setTitle(title)` - name what is open (a client's name) for the pane header and breadcrumbs.
 */
export interface ViewNav {
  inPane: boolean;
  open: (route: string) => void;
  href: (route: string) => string;
  say: ((text: string) => void) | null;
  changed: () => void;
  setTitle: (title: string | null) => void;
}

const ViewNavContext = createContext<ViewNav | null>(null);

export function ViewNavProvider({ value, children }: { value: ViewNav; children: ReactNode }) {
  return <ViewNavContext.Provider value={value}>{children}</ViewNavContext.Provider>;
}

/** The classic-page behaviour, used whenever no pane has provided one. */
export function useViewNav(): ViewNav {
  const ctx = useContext(ViewNavContext);
  const router = useRouter();
  const open = useCallback((route: string) => router.push(route), [router]);
  const classic = useMemo<ViewNav>(
    () => ({
      inPane: false,
      open,
      href: (route) => route,
      say: null,
      changed: () => {},
      setTitle: () => {},
    }),
    [open]
  );
  return ctx ?? classic;
}

/** Name what the view shows: the pane header in the workspace, the tab title on a page. */
export function useViewTitle(title: string | null | undefined) {
  const nav = useViewNav();
  const { inPane, setTitle } = nav;
  useEffect(() => {
    if (!title) return;
    if (inPane) setTitle(title);
    else document.title = `${title} — Astra for Wealth`;
  }, [title, inPane, setTitle]);
}

/** A link between views. Inside the pane a pane view opens in the pane (a modified click still
 * opens the real URL in a new tab); every other route is an ordinary link to its page. */
export function ViewLink({
  href,
  children,
  onClick,
  ...rest
}: { href: string; children: ReactNode } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">) {
  const nav = useViewNav();
  if (!nav.inPane || !matchPaneView(href)) {
    return (
      <Link href={href} onClick={onClick} {...rest}>
        {children}
      </Link>
    );
  }
  return (
    <a
      href={nav.href(href)}
      onClick={(e: MouseEvent<HTMLAnchorElement>) => {
        onClick?.(e);
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        e.preventDefault();
        nav.open(href);
      }}
      {...rest}
    >
      {children}
    </a>
  );
}
