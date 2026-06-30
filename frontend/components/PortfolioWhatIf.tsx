"use client";
import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal, ArrowRight, TrendingUp, Activity, ShieldAlert } from "lucide-react";
import { Spinner } from "./ui";
import { api } from "@/lib/api";
import { money, titleCase } from "@/lib/format";

const SCENARIOS: Record<string, string> = {
  gfc_2008: "GFC 2008", covid_2020: "COVID 2020", rates_shock: "Rates shock", inflation_spike: "Inflation spike",
};

/** Shift `tilt` (fraction of total) between defensive (cash/fixed income) and equity. */
function tiltAllocation(base: Record<string, number>, tilt: number): Record<string, number> {
  const out = { ...base };
  const total = Object.values(base).reduce((s, v) => s + v, 0) || 1;
  let move = Math.abs(tilt) * total;
  if (tilt >= 0) {
    for (const d of ["cash", "fixed_income"]) {
      const take = Math.min(out[d] || 0, move);
      out[d] = (out[d] || 0) - take; out.equity = (out.equity || 0) + take; move -= take;
      if (move <= 0) break;
    }
  } else {
    const take = Math.min(out.equity || 0, move);
    out.equity = (out.equity || 0) - take; out.fixed_income = (out.fixed_income || 0) + take;
  }
  return out;
}

export default function PortfolioWhatIf({ allocation }: { allocation: Record<string, number> }) {
  const base = Object.fromEntries(Object.entries(allocation || {}).filter(([, v]) => v > 0));
  const [tilt, setTilt] = useState(0);
  const [cur, setCur] = useState<any>(null);
  const [prop, setProp] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { ref.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }, []);

  useEffect(() => { api("/api/core/portfolio-whatif", { body: { allocation: base } }).then(setCur); }, []); // eslint-disable-line

  useEffect(() => {
    setBusy(true);
    const t = setTimeout(() => {
      api("/api/core/portfolio-whatif", { body: { allocation: tiltAllocation(base, tilt) } })
        .then(setProp).finally(() => setBusy(false));
    }, 250);
    return () => clearTimeout(t);
  }, [tilt]); // eslint-disable-line

  if (!cur || !prop) return <Spinner />;
  const dRet = prop.risk.expected_return - cur.risk.expected_return;
  const dVar = prop.risk.var_95_1y - cur.risk.var_95_1y;

  return (
    <div ref={ref} className="card p-4 mb-6 border-gold/40 scroll-mt-4">
      <div className="flex items-center gap-2 mb-1">
        <SlidersHorizontal size={17} className="text-gold" />
        <h3 className="font-semibold text-ink">Portfolio what-if</h3>
        <span className="text-xs text-ink-muted">Test an allocation change against return, risk and market shocks</span>
      </div>

      {/* Tilt control */}
      <div className="mt-3 mb-4">
        <div className="flex justify-between text-sm mb-1">
          <span className="text-ink-soft">Growth tilt — shift between defensive and equity</span>
          <span className="font-semibold text-ink tabular-nums">{tilt > 0 ? "+" : ""}{Math.round(tilt * 100)}% to growth{busy && " · updating…"}</span>
        </div>
        <input type="range" min={-0.2} max={0.2} step={0.05} value={tilt}
          onChange={(e) => setTilt(Number(e.target.value))} className="w-full accent-gold" />
        <div className="flex justify-between text-[11px] text-ink-muted"><span>−20% (de-risk)</span><span>current</span><span>+20% (growth)</span></div>
      </div>

      {/* Return / risk comparison */}
      <div className="grid sm:grid-cols-3 gap-3 mb-4">
        <Compare icon={TrendingUp} label="Expected return" cur={`${(cur.risk.expected_return * 100).toFixed(1)}%`}
          prop={`${(prop.risk.expected_return * 100).toFixed(1)}%`} delta={`${dRet >= 0 ? "+" : ""}${(dRet * 100).toFixed(1)}%`} good={dRet >= 0} />
        <Compare icon={Activity} label="Volatility" cur={`${(cur.risk.volatility * 100).toFixed(1)}%`}
          prop={`${(prop.risk.volatility * 100).toFixed(1)}%`}
          delta={`${prop.risk.volatility >= cur.risk.volatility ? "+" : ""}${((prop.risk.volatility - cur.risk.volatility) * 100).toFixed(1)}%`}
          good={prop.risk.volatility <= cur.risk.volatility} />
        <Compare icon={ShieldAlert} label="1y 95% VaR" cur={money(cur.risk.var_95_1y)} prop={money(prop.risk.var_95_1y)}
          delta={`${dVar >= 0 ? "+" : ""}${money(dVar)}`} good={dVar <= 0} />
      </div>

      {/* Stress scenarios */}
      <div className="tile-label mb-2">Loss under a market shock (proposed vs current)</div>
      <div className="space-y-2">
        {Object.keys(SCENARIOS).map((k) => {
          const c = cur.stress[k]?.impact_value || 0;
          const pr = prop.stress[k]?.impact_value || 0;
          const worst = Math.min(...Object.keys(SCENARIOS).flatMap((s) => [cur.stress[s]?.impact_value || 0, prop.stress[s]?.impact_value || 0]));
          const w = (v: number) => `${Math.min(100, (Math.abs(v) / Math.abs(worst || 1)) * 100)}%`;
          return (
            <div key={k} className="grid grid-cols-[120px_1fr_auto] items-center gap-3 text-sm">
              <span className="text-ink-soft">{SCENARIOS[k]}</span>
              <div className="relative h-5 rounded bg-navy-50 overflow-hidden">
                <div className="absolute inset-y-0 left-0 bg-navy-300/50" style={{ width: w(c) }} />
                <div className="absolute inset-y-0 left-0 bg-critical/70" style={{ width: w(pr) }} />
              </div>
              <span className={`tabular-nums text-xs ${pr < c ? "text-critical" : "text-positive"}`}>{money(pr)}</span>
            </div>
          );
        })}
      </div>
      <p className="text-[11px] text-ink-muted mt-2">Red = proposed allocation · grey = current. Figures are modelled on long-run capital-market assumptions; no orders are placed.</p>
    </div>
  );
}

function Compare({ icon: Icon, label, cur, prop, delta, good }: any) {
  return (
    <div className="rounded-lg border border-navy-100 bg-surface p-3">
      <div className="flex items-center gap-1.5 text-xs text-ink-muted mb-1.5"><Icon size={13} /> {label}</div>
      <div className="flex items-center gap-2">
        <span className="text-sm text-ink-muted tabular-nums">{cur}</span>
        <ArrowRight size={13} className="text-ink-muted" />
        <span className="text-lg font-semibold text-ink tabular-nums">{prop}</span>
      </div>
      <div className={`text-xs mt-0.5 ${good ? "text-positive" : "text-caution"}`}>{delta}</div>
    </div>
  );
}
