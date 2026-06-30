"use client";
import { PieChart, Pie, Cell, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell as BCell,
  AreaChart, Area, ReferenceLine } from "recharts";
import { ASSET_COLORS, money, titleCase } from "@/lib/format";

/** Retirement balance fan: p10 / median / p90 portfolio value across retirement age. */
export function RetirementFan({
  data, retirementAge,
}: {
  data: { age: number; p10: number; median: number; p90: number }[];
  retirementAge?: number;
}) {
  const fmt = (v: number) => (v >= 1e6 ? `$${(v / 1e6).toFixed(1)}m` : `$${Math.round(v / 1e3)}k`);
  return (
    <ResponsiveContainer width="100%" height={210}>
      <AreaChart data={data} margin={{ left: 4, right: 8, top: 8 }}>
        <defs>
          <linearGradient id="fan90" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#5d86a3" stopOpacity={0.25} />
            <stop offset="100%" stopColor="#5d86a3" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <XAxis dataKey="age" fontSize={11} stroke="#9aa7b2" tickFormatter={(v) => `${v}`} minTickGap={24} />
        <YAxis fontSize={11} stroke="#9aa7b2" tickFormatter={fmt} width={48} />
        <Tooltip formatter={(v: any, n: any) => [money(Number(v)), titleCase(String(n))]} labelFormatter={(l) => `Age ${l}`} />
        <Area dataKey="p90" stroke="none" fill="url(#fan90)" />
        <Area dataKey="p10" stroke="none" fill="#ffffff" />
        <Area dataKey="median" stroke="#1f3a52" strokeWidth={2} fill="none" />
        <ReferenceLine y={0} stroke="#c8503c" strokeDasharray="3 3" />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function AllocationDonut({
  data,
  size = 180,
}: {
  data: Record<string, number>;
  size?: number;
}) {
  const entries = Object.entries(data || {}).filter(([, v]) => v > 0);
  const total = entries.reduce((s, [, v]) => s + v, 0);
  const chart = entries.map(([k, v]) => ({ name: k, value: v }));
  return (
    <div className="flex items-center gap-5">
      <div style={{ width: size, height: size }} className="relative">
        <ResponsiveContainer>
          <PieChart>
            <Pie data={chart} dataKey="value" innerRadius={size * 0.32} outerRadius={size * 0.48} paddingAngle={2} stroke="none">
              {chart.map((e) => (
                <Cell key={e.name} fill={ASSET_COLORS[e.name] || "#9aa7b2"} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <div className="text-[10px] uppercase tracking-wide text-ink-muted">Total</div>
          <div className="text-sm font-semibold text-ink">{money(total)}</div>
        </div>
      </div>
      <div className="space-y-1.5">
        {chart.map((e) => (
          <div key={e.name} className="flex items-center gap-2 text-sm">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: ASSET_COLORS[e.name] || "#9aa7b2" }} />
            <span className="text-ink-soft w-28">{titleCase(e.name)}</span>
            <span className="text-ink-muted tabular-nums">{((e.value / total) * 100).toFixed(0)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function DriftBars({
  current,
  target,
}: {
  current: Record<string, number>;
  target: Record<string, number>;
}) {
  const classes = Array.from(new Set([...Object.keys(target), ...Object.keys(current)])).filter(
    (c) => c !== "cash"
  );
  const data = classes.map((c) => ({
    name: titleCase(c),
    drift: ((current[c] || 0) - (target[c] || 0)) * 100,
  }));
  return (
    <ResponsiveContainer width="100%" height={Math.max(120, data.length * 38)}>
      <BarChart data={data} layout="vertical" margin={{ left: 10, right: 20 }}>
        <XAxis type="number" domain={["dataMin", "dataMax"]} tickFormatter={(v) => `${v.toFixed(0)}%`} fontSize={11} stroke="#9aa7b2" />
        <YAxis type="category" dataKey="name" width={90} fontSize={12} stroke="#5d6f80" />
        <Tooltip formatter={(v: any) => `${Number(v).toFixed(1)}%`} />
        <Bar dataKey="drift" radius={[0, 4, 4, 0]}>
          {data.map((d, i) => (
            <BCell key={i} fill={d.drift > 0 ? "#b9852b" : "#5d86a3"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
