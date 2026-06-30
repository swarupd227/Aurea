"use client";
import { useState } from "react";
import { Users, Home, TrendingUp, Target } from "lucide-react";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { Spinner } from "@/components/ui";

export default function FamilyAggregatePage() {
  const [personId, setPersonId] = useState("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  async function load() {
    if (!personId.trim()) return;
    setLoading(true);
    setErr("");
    setData(null);
    try {
      const result = await api(`/api/studio/family?person_id=${personId.trim()}`);
      setData(result);
    } catch (e: any) {
      setErr(e.message || "Failed to load family aggregate");
    }
    setLoading(false);
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-6">
        <h1 className="font-serif text-2xl text-ink">Family Wealth Aggregate</h1>
        <p className="text-sm text-ink-muted mt-1">
          Enter a Person ID to see the consolidated family view across all linked households (intergenerational, spouse, parent/child).
        </p>
      </div>

      <div className="card p-5 mb-5">
        <div className="flex gap-3">
          <input
            className="input flex-1"
            placeholder="Person UUID (e.g. from household view)"
            value={personId}
            onChange={(e) => setPersonId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()}
          />
          <button className="btn-primary" onClick={load} disabled={loading || !personId.trim()}>
            {loading ? "Loading…" : "Load"}
          </button>
        </div>
        {err && <p className="text-sm text-critical mt-2">{err}</p>}
      </div>

      {loading && <Spinner label="Building family aggregate…" />}

      {data && (
        <div className="space-y-4">
          {/* Summary tiles */}
          <div className="grid grid-cols-3 gap-4">
            <div className="card p-4 flex items-center gap-3">
              <div className="h-10 w-10 rounded-xl bg-navy-100 flex items-center justify-center">
                <Home size={18} className="text-navy-600" />
              </div>
              <div>
                <div className="text-xl font-bold text-ink">{data.household_count}</div>
                <div className="text-xs text-ink-muted">Households</div>
              </div>
            </div>
            <div className="card p-4 flex items-center gap-3">
              <div className="h-10 w-10 rounded-xl bg-gold-soft/40 flex items-center justify-center">
                <TrendingUp size={18} className="text-gold-dark" />
              </div>
              <div>
                <div className="text-xl font-bold text-ink">{money(data.total_family_aum)}</div>
                <div className="text-xs text-ink-muted">Total family AUM</div>
              </div>
            </div>
            <div className="card p-4 flex items-center gap-3">
              <div className="h-10 w-10 rounded-xl bg-positive/10 flex items-center justify-center">
                <Target size={18} className="text-positive" />
              </div>
              <div>
                <div className="text-xl font-bold text-ink">{data.goal_count}</div>
                <div className="text-xs text-ink-muted">Goals across family</div>
              </div>
            </div>
          </div>

          {/* Households */}
          {data.households?.length > 0 && (
            <div className="card p-5">
              <div className="font-semibold text-ink mb-3 flex items-center gap-2">
                <Users size={16} className="text-navy-600" /> Linked households
              </div>
              <div className="space-y-2">
                {data.households.map((h: any) => (
                  <div key={h.id} className="flex items-center gap-3 p-3 rounded-xl border border-navy-100">
                    <Home size={14} className="text-navy-400 shrink-0" />
                    <div className="flex-1">
                      <div className="font-medium text-sm text-ink">{h.name}</div>
                      <div className="text-xs text-ink-muted">{h.segment}</div>
                    </div>
                    <div className="text-xs font-mono text-ink-muted">{h.id.slice(0, 8)}…</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Goals */}
          {data.goals?.length > 0 && (
            <div className="card p-5">
              <div className="font-semibold text-ink mb-3 flex items-center gap-2">
                <Target size={16} className="text-navy-600" /> Family goals
              </div>
              <div className="space-y-2">
                {data.goals.map((g: any, i: number) => (
                  <div key={i} className="flex items-center gap-3 p-3 rounded-xl border border-navy-100">
                    <div className="flex-1">
                      <div className="font-medium text-sm text-ink">{g.name}</div>
                      <div className="text-xs text-ink-muted capitalize">{g.kind}</div>
                    </div>
                    <div className="text-sm font-medium text-ink">{money(g.target_amount)}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Family member IDs */}
          <div className="card p-5">
            <div className="font-semibold text-ink mb-3 flex items-center gap-2">
              <Users size={16} className="text-navy-600" /> Family members ({data.person_ids?.length})
            </div>
            <div className="flex flex-wrap gap-2">
              {data.person_ids?.map((id: string) => (
                <button key={id} onClick={() => setPersonId(id)}
                        className="chip bg-navy-50 text-navy-700 hover:bg-navy-100 border border-navy-100 font-mono text-xs">
                  {id.slice(0, 8)}…
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
