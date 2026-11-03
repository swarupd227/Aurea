"use client";

import { useEffect, useState } from "react";
import RetirementPlanner from "@/components/RetirementPlanner";
import { SkeletonList } from "@/components/ui";
import { getUser } from "@/lib/api";
import { CanvasHouseholdPicker } from "./CanvasView";
import { AllClients, ViewHeader } from "./ViewHeader";

const ROUTE = "/canvas/retirement";
const BASE = "/api/canvas/retirement";

/** The signed-in role, read after mount: localStorage is not there on the server, and reading it
 * during render would draw the staff view first and the client's after. undefined = not read yet. */
export function useRole(): string | null | undefined {
  const [role, setRole] = useState<string | null | undefined>(undefined);
  useEffect(() => setRole(getUser()?.role ?? null), []);
  return role;
}

/** The client's retirement projection. A client sees their own household whatever is asked for (the
 * server resolves it); staff pick a household, then see what that client sees. */
export function CanvasRetirementView({ household = null }: { household?: string | null }) {
  const role = useRole();
  const [name, setName] = useState<string | null>(null);
  useEffect(() => setName(null), [household]);

  let body;
  if (role === undefined) body = <SkeletonList count={4} />;
  else if (role === "client") {
    body = (
      <>
        <ViewHeader title="Retirement" meta={<span className="chip bg-border-soft text-ink-soft">estimate</span>} />
        <RetirementPlanner basePath={BASE} client />
      </>
    );
  } else if (!household) body = <CanvasHouseholdPicker route={ROUTE} title="Retirement" testId="canvas-retirement-households" />;
  else {
    body = (
      <>
        <ViewHeader
          title={name ? `Retirement · ${name}` : "Retirement"}
          meta={<>
            <span className="chip bg-border-soft text-ink-soft">client view</span>
            <span className="chip bg-border-soft text-ink-soft">estimate</span>
          </>}
          actions={<AllClients href={ROUTE} />}
        />
        <RetirementPlanner key={household} basePath={BASE} client query={{ household_id: household }}
          onLoad={(p) => setName(p?.household?.name ?? null)} />
      </>
    );
  }
  return <div className="view-container" data-testid="canvas-retirement-view">{body}</div>;
}
