/** Who knows whom around a prospect or client, two links out (aurea_core/network_map.py, B-201). A plain SVG, laid out
 * in rings: the centre, who they know, who those know. A link a person recorded is solid (from a LinkedIn export, in
 * the info colour); a fact the platform holds (a family, a household, an adviser, a logged touch) is dashed. */

export interface MapNode { key: string; type: string; id: string; name: string; ring: number; chips: string[]; you?: boolean }
export interface MapEdge {
  a: string; b: string; labels: string[]; kinds: string[]; recorded: boolean; linkedin: boolean; simulated: boolean;
  evidence: { text: string; by: string | null; on: string | null }[];
}
export interface NetworkMapPayload {
  centre: { type: string; id: string; name: string; key: string };
  depth: number; nodes: MapNode[]; edges: MapEdge[];
  counts: { nodes: number; edges: number; recorded: number; facts: number; linkedin: number; truncated: number };
  simulated?: boolean; as_of?: string;
}

const W = 600, H = 380, R1 = 105, R2 = 175, STRETCH = 1.5;
const FILL: Record<string, string> = { person: "fill-ok", contact: "fill-info", entity: "fill-ink-muted", user: "fill-ink" };
const TYPE: Record<string, string> = { person: "client", contact: "contact", entity: "entity", user: "staff" };

function layout(map: NetworkMapPayload): Record<string, { x: number; y: number }> {
  const at = (angle: number, r: number) => ({ x: W / 2 + Math.cos(angle) * r * STRETCH, y: H / 2 + Math.sin(angle) * r });
  const pos: Record<string, { x: number; y: number }> = { [map.centre.key]: { x: W / 2, y: H / 2 } };
  const ring1 = map.nodes.filter((n) => n.ring === 1);
  const angle: Record<string, number> = {};
  ring1.forEach((n, i) => { angle[n.key] = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(ring1.length, 1); pos[n.key] = at(angle[n.key], R1); });
  // Each outer node sits in the sector of the first inner node it is linked to.
  const groups: Record<string, string[]> = {};
  for (const n of map.nodes.filter((x) => x.ring === 2)) {
    const e = map.edges.find((e) => (e.a === n.key && angle[e.b] !== undefined) || (e.b === n.key && angle[e.a] !== undefined));
    const parent = e ? (e.a === n.key ? e.b : e.a) : ring1[0]?.key;
    (groups[parent || ""] ||= []).push(n.key);
  }
  const sector = (2 * Math.PI) / Math.max(ring1.length, 1);
  for (const [parent, keys] of Object.entries(groups)) {
    const base = angle[parent] ?? 0;
    keys.forEach((k, i) => { pos[k] = at(base + (keys.length === 1 ? 0 : sector * 0.8 * (i / (keys.length - 1) - 0.5)), R2); });
  }
  return pos;
}

const short = (s: string) => (s.length > 18 ? `${s.slice(0, 17)}…` : s);

/** The graph itself, shared by the card and the Growth pipeline page. */
export function NetworkGraph({ map }: { map: NetworkMapPayload }) {
  const pos = layout(map);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Network around ${map.centre.name}`} data-testid="network-graph">
      {map.edges.map((e, i) => {
        const a = pos[e.a], b = pos[e.b];
        if (!a || !b) return null;
        const tone = e.linkedin ? "stroke-info" : e.recorded ? "stroke-ink" : "stroke-ink-muted";
        return (
          <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={tone} strokeWidth={e.recorded ? 1.6 : 1}
            strokeDasharray={e.recorded ? undefined : "4 3"} data-testid={e.recorded ? "network-edge-recorded" : "network-edge-fact"}>
            <title>{e.labels.join(", ")}{e.evidence.length ? ` · ${e.evidence.map((x) => x.text).join("; ")}` : ""}</title>
          </line>
        );
      })}
      {map.nodes.map((n) => {
        const p = pos[n.key];
        if (!p) return null;
        const centre = n.key === map.centre.key;
        return (
          <g key={n.key} data-testid="network-node">
            <circle cx={p.x} cy={p.y} r={centre ? 9 : 6} className={`${FILL[n.type] || "fill-ink-muted"} ${centre ? "stroke-ink" : "stroke-surface"}`} strokeWidth={centre ? 2.5 : 1.5}>
              <title>{n.name} · {n.chips.join(", ")}</title>
            </circle>
            <text x={p.x} y={p.y + (centre ? 22 : 17)} textAnchor="middle" className={centre ? "fill-ink" : "fill-ink-muted"} fontSize={centre ? 12 : 10}>
              {short(n.you ? `${n.name} (you)` : n.name)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function NetworkLegend({ map }: { map: NetworkMapPayload }) {
  const c = map.counts;
  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(TYPE).map(([t, label]) => (
        <span key={t} className="chip text-ink-muted"><svg width="8" height="8" className="mr-1 inline" aria-hidden><circle cx="4" cy="4" r="4" className={FILL[t]} /></svg>{label}</span>
      ))}
      <span className="chip text-ink tabular-nums">{c.recorded} recorded</span>
      {c.linkedin > 0 && <span className="chip text-info tabular-nums">{c.linkedin} from LinkedIn</span>}
      <span className="chip text-ink-muted tabular-nums">{c.facts} on file as fact</span>
      {c.truncated > 0 && <span className="chip text-ink-muted tabular-nums">{c.truncated} more not drawn</span>}
      {map.simulated && <span className="chip text-warn" data-testid="network-simulated">simulated</span>}
    </div>
  );
}

/** The recorded links on the map, each with its evidence. */
export function RecordedLinks({ map }: { map: NetworkMapPayload }) {
  const name = Object.fromEntries(map.nodes.map((n) => [n.key, n.name]));
  const rows = map.edges.filter((e) => e.recorded);
  if (!rows.length) return null;
  return (
    <table className="w-full text-xs" data-testid="network-recorded">
      <tbody className="divide-y divide-border-soft">
        {rows.map((e, i) => (
          <tr key={i}>
            <td className="px-3 py-1.5 text-ink">{name[e.a]} · {name[e.b]}</td>
            <td className="px-3 py-1.5">
              <div className="flex flex-wrap gap-1">
                {e.labels.map((l, j) => <span key={j} className="chip text-ink-muted">{l}</span>)}
                {e.linkedin && <span className="chip text-info">LinkedIn</span>}
                {e.simulated && <span className="chip text-warn">simulated</span>}
              </div>
            </td>
            <td className="hidden px-3 py-1.5 text-[11px] text-ink-muted cq-sm:table-cell">{e.evidence.map((x) => x.text).join("; ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function NetworkMapCard({ result }: { result: NetworkMapPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="network-map-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">Network · {result.centre.name}</div>
        <span className="chip text-ink-muted tabular-nums">{result.counts.nodes - 1} around</span>
        <span className="chip text-ink-muted">{result.depth} links out</span>
      </div>
      <div className="border-b border-border-soft px-2 py-2"><NetworkGraph map={result} /></div>
      <div className="border-b border-border-soft px-3 py-2"><NetworkLegend map={result} /></div>
      <RecordedLinks map={result} />
    </div>
  );
}

export interface LinkedInImportPayload {
  rm: string; rows: number; matched: number; to_link: number; already: number; unmatched: number; ambiguous: number;
  linked?: number; linking: { name: string; type: string; connected_on: string | null }[];
  unmatched_names: string[]; ambiguous_names: string[];
}

/** What an RM's own export linked, matched and skipped. Rows matching nobody are listed, never made contacts. */
export function LinkedInImportCard({ result }: { result: LinkedInImportPayload }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface" data-testid="linkedin-import-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-soft px-3 py-2">
        <div className="text-sm font-medium text-ink">LinkedIn connections · {result.rm}</div>
        <span className="chip text-ink-muted tabular-nums">{result.rows} rows</span>
        <span className="chip text-ok tabular-nums">{result.linked ?? result.to_link} linked</span>
        <span className="chip text-ink-muted tabular-nums">{result.already} already</span>
        <span className="chip text-ink-muted tabular-nums">{result.unmatched} match nobody</span>
        {result.ambiguous > 0 && <span className="chip text-warn tabular-nums">{result.ambiguous} match two</span>}
      </div>
      {result.linking.length > 0 && (
        <table className="w-full text-xs"><tbody className="divide-y divide-border-soft">
          {result.linking.map((l, i) => (
            <tr key={i}><td className="px-3 py-1.5 text-ink">{l.name}</td><td className="px-3 py-1.5 text-ink-muted">{l.type === "person" ? "client" : "contact"}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink-muted">{l.connected_on || "no date"}</td></tr>
          ))}
        </tbody></table>
      )}
      {(result.unmatched_names.length > 0 || result.ambiguous_names.length > 0) && (
        <div className="flex flex-wrap gap-1 border-t border-border-soft px-3 py-2">
          {result.unmatched_names.map((n) => <span key={n} className="chip text-ink-muted">{n}</span>)}
          {result.ambiguous_names.map((n) => <span key={n} className="chip text-warn">{n}</span>)}
        </div>
      )}
    </div>
  );
}
