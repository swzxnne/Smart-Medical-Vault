"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { FlagBadge } from "@/components/FlagBadge";
import { ReviewBadge } from "@/components/ReviewBadge";
import { formatDate } from "@/lib/format";
import { formatValue, sourceHref } from "@/lib/health";
import type { Observation } from "@/lib/types";

// Categorical slots in fixed order (validated: CVD-safe adjacent pairs on a light surface).
// Slots 3-4 are under 3:1 contrast, so charts always carry a legend, end labels and a table view.
const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
const INK = { secondary: "#52514e", muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7", surface: "#ffffff" };

export interface ChartSeries {
  code: string;
  label: string;
  points: Observation[]; // sorted by observed_at, all in the same unit
}

// `iso` keeps the calendar date as printed; converting `t` back through toISOString() would
// shift it a day in timezones ahead of UTC.
type Row = { t: number; iso: string } & Record<string, number | string | Observation | undefined>;

function toTime(iso: string) {
  return new Date(`${iso}T00:00:00`).getTime();
}

function buildRows(series: ChartSeries[]): Row[] {
  const rows = new Map<number, Row>();
  for (const s of series) {
    for (const p of s.points) {
      if (!p.observed_at || p.value_num === null) continue;
      const t = toTime(p.observed_at);
      const row = rows.get(t) ?? ({ t, iso: p.observed_at } as Row);
      row[s.code] = p.value_num;
      row[`${s.code}__obs`] = p;
      rows.set(t, row);
    }
  }
  return [...rows.values()].sort((a, b) => a.t - b.t);
}

/** Round axis bounds and ticks to 1/2/5 x 10^n steps. */
function niceTicks(min: number, max: number, target = 5): number[] {
  const range = max - min || Math.abs(max) || 1;
  const rough = range / target;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? 10 * magnitude;
  const start = Math.floor(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 0.5; v += step) ticks.push(Number(v.toFixed(10)));
  if (ticks.at(-1)! < max) ticks.push(Number((ticks.at(-1)! + step).toFixed(10)));
  return ticks;
}

function shortDate(t: number) {
  return new Date(t).toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

export function HealthChart({ title, unit, series }: { title: string; unit: string; series: ChartSeries[] }) {
  const router = useRouter();
  const [showTable, setShowTable] = useState(false);
  const rows = useMemo(() => buildRows(series), [series]);
  const colors = Object.fromEntries(series.map((s, i) => [s.code, SERIES_COLORS[i % SERIES_COLORS.length]]));

  // A reference band is only meaningful for a single series, using the range printed on its latest report.
  const latest = series.length === 1 ? series[0].points.at(-1) : undefined;
  const band = latest && (latest.ref_low !== null || latest.ref_high !== null) ? latest : undefined;

  const values = rows.flatMap((r) => series.map((s) => r[s.code]).filter((v): v is number => typeof v === "number"));
  if (band?.ref_low != null) values.push(band.ref_low);
  if (band?.ref_high != null) values.push(band.ref_high);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min || Math.abs(max) || 1) * 0.1;
  // Percentages like SpO2 can't exceed 100; don't let tick rounding draw an impossible 110%.
  const ceiling = unit === "%" && max <= 100 ? 100 : Infinity;
  const ticks = niceTicks(Math.max(0, min - pad), Math.min(max + pad, ceiling)).filter((t) => t <= ceiling);
  const domain: [number, number] = [ticks[0], ticks.at(-1)!];
  const xPad = rows.length > 1 ? (rows.at(-1)!.t - rows[0].t) * 0.04 : 30 * 86400000;

  const lastIndex = Object.fromEntries(
    series.map((s) => [s.code, rows.map((r) => r[s.code] !== undefined).lastIndexOf(true)]),
  );

  return (
    <figure className="card space-y-3">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3 className="font-semibold text-slate-800">{title}</h3>
          <p className="text-xs text-slate-500">{unit}</p>
        </div>
        <button className="text-xs font-medium text-teal-700 hover:underline" onClick={() => setShowTable((v) => !v)}>
          {showTable ? "Show chart" : "Show table"}
        </button>
      </figcaption>

      {series.length > 1 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
          {series.map((s) => (
            <li key={s.code} className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-0.5 w-4 rounded" style={{ background: colors[s.code] }} />
              {s.label}
            </li>
          ))}
        </ul>
      )}

      {showTable ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm tabular-nums">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                <th className="py-1.5 pr-4 font-medium">Date</th>
                {series.map((s) => (
                  <th key={s.code} className="py-1.5 pr-4 font-medium">{s.label}</th>
                ))}
                <th className="py-1.5 font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const obs = series.map((s) => row[`${s.code}__obs`] as Observation | undefined);
                const first = obs.find(Boolean);
                const href = first ? sourceHref(first.document_id, first.page_no) : null;
                return (
                  <tr key={row.t} className="border-b border-slate-100">
                    <td className="py-1.5 pr-4">{formatDate(row.iso)}</td>
                    {obs.map((o, i) => (
                      <td key={series[i].code} className="py-1.5 pr-4">
                        {o ? (
                          <span className="inline-flex items-center gap-2">
                            {formatValue(o)} <FlagBadge flag={o.flag} />
                            {!o.verified && <ReviewBadge note={o.review_note} compact />}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                    ))}
                    <td className="py-1.5 text-xs">
                      {href ? (
                        <a className="text-teal-700 hover:underline" href={href}>{first?.document_name ?? "Document"}</a>
                      ) : (
                        <span className="text-slate-500">Entered manually</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="h-64" role="img" aria-label={`${title} over time. Use "Show table" for the values.`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 12, right: series.length <= 4 ? 96 : 16, bottom: 4, left: 0 }}>
              <CartesianGrid vertical={false} stroke={INK.grid} strokeWidth={1} />
              {band && (
                <ReferenceArea
                  y1={band.ref_low ?? domain[0]}
                  y2={band.ref_high ?? domain[1]}
                  fill="#e1e0d9"
                  fillOpacity={0.45}
                  stroke="none"
                  ifOverflow="extendDomain"
                  label={{ value: "Report's reference range", position: "insideTopLeft", fill: INK.muted, fontSize: 11 }}
                />
              )}
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={[rows[0]?.t - xPad, (rows.at(-1)?.t ?? 0) + xPad]}
                tickFormatter={shortDate}
                tick={{ fill: INK.muted, fontSize: 11 }}
                axisLine={{ stroke: INK.axis }}
                tickLine={false}
                minTickGap={40}
              />
              <YAxis
                domain={domain}
                ticks={ticks}
                tick={{ fill: INK.muted, fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={44}
                allowDecimals
              />
              <Tooltip
                cursor={{ stroke: INK.axis, strokeWidth: 1 }}
                content={({ active, label }) => {
                  if (!active || label === undefined) return null;
                  const row = rows.find((r) => r.t === label);
                  if (!row) return null;
                  const first = series.map((s) => row[`${s.code}__obs`] as Observation | undefined).find(Boolean);
                  return (
                    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-sm">
                      <p className="mb-1 text-slate-500">{formatDate(row.iso)}</p>
                      {series.map((s) => {
                        const o = row[`${s.code}__obs`] as Observation | undefined;
                        return o ? (
                          <p key={s.code} className="flex items-center gap-2">
                            <span aria-hidden className="inline-block h-0.5 w-3 rounded" style={{ background: colors[s.code] }} />
                            <strong className="text-sm text-slate-900">{formatValue(o)}</strong>
                            <span className="text-slate-500">{s.label}</span>
                            <FlagBadge flag={o.flag} />
                            {!o.verified && <ReviewBadge note={o.review_note} compact />}
                          </p>
                        ) : null;
                      })}
                      <p className="mt-1 text-slate-400">
                        {first?.document_name ? `${first.document_name} · click a point to open` : "Entered manually"}
                      </p>
                    </div>
                  );
                }}
              />
              {series.map((s) => (
                <Line
                  key={s.code}
                  dataKey={s.code}
                  name={s.label}
                  type="linear"
                  stroke={colors[s.code]}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  connectNulls
                  isAnimationActive={false}
                  activeDot={{ r: 6, stroke: INK.surface, strokeWidth: 2 }}
                  dot={(props: { cx?: number; cy?: number; index?: number; payload?: Row }) => {
                    const { cx, cy, index, payload } = props;
                    if (cx === undefined || cy === undefined || !payload || payload[s.code] === undefined) {
                      return <g key={`${s.code}-${index}`} />;
                    }
                    const obs = payload[`${s.code}__obs`] as Observation;
                    const href = sourceHref(obs.document_id, obs.page_no);
                    const isLast = index === lastIndex[s.code];
                    return (
                      <g
                        key={`${s.code}-${index}`}
                        style={{ cursor: href ? "pointer" : "default" }}
                        onClick={() => href && router.push(href)}
                      >
                        {/* Transparent hit area: bigger than the painted mark. */}
                        <circle cx={cx} cy={cy} r={12} fill="transparent" />
                        <circle cx={cx} cy={cy} r={4} fill={colors[s.code]} stroke={INK.surface} strokeWidth={2} />
                        {isLast && series.length <= 4 && (
                          <text x={cx + 10} y={cy + 4} fontSize={11} fill={INK.secondary}>
                            {s.label}
                          </text>
                        )}
                      </g>
                    );
                  }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </figure>
  );
}
