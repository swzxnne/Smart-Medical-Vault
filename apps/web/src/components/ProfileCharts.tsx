"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AddReadingForm } from "@/components/AddReadingForm";
import { type ChartSeries, HealthChart } from "@/components/HealthChart";
import { useApi } from "@/lib/api";
import type { Catalog, Observation } from "@/lib/types";

const SHORT_LABELS: Record<string, string> = { bp_systolic: "Systolic", bp_diastolic: "Diastolic" };

/** One chart per group and unit (never two y-scales on one chart). */
function buildCharts(observations: Observation[], catalog: Catalog) {
  const order = catalog.metrics.map((m) => m.code);
  const charts = new Map<string, { title: string; unit: string; series: Map<string, ChartSeries> }>();
  for (const obs of observations) {
    if (!obs.group || !obs.observed_at || obs.value_num === null) continue;
    const unit = obs.unit ?? "";
    const key = `${obs.group}|${unit}`;
    const chart = charts.get(key) ?? { title: catalog.groups[obs.group] ?? obs.group, unit, series: new Map() };
    const series = chart.series.get(obs.code) ?? { code: obs.code, label: SHORT_LABELS[obs.code] ?? obs.name, points: [] };
    series.points.push(obs);
    chart.series.set(obs.code, series);
    charts.set(key, chart);
  }
  return [...charts.entries()]
    .map(([key, c]) => ({
      key,
      title: c.title,
      unit: c.unit,
      series: [...c.series.values()].sort((a, b) => order.indexOf(a.code) - order.indexOf(b.code)),
    }))
    .sort((a, b) => order.indexOf(a.series[0].code) - order.indexOf(b.series[0].code));
}

export function ProfileCharts({ profileId, catalog }: { profileId: string; catalog: Catalog }) {
  const api = useApi();
  const [observations, setObservations] = useState<Observation[] | null>(null);

  const load = useCallback(() => {
    api<Observation[]>(`/profiles/${profileId}/observations`).then(setObservations).catch(() => setObservations([]));
  }, [api, profileId]);
  useEffect(load, [load]);

  const charts = useMemo(() => buildCharts(observations ?? [], catalog), [observations, catalog]);

  return (
    <div className="space-y-4">
      <AddReadingForm profileId={profileId} catalog={catalog} onAdded={load} />
      {observations === null && <p className="text-sm text-slate-500">Loading…</p>}
      {observations?.length === 0 && (
        <p className="card text-sm text-slate-600">No measurements yet. Upload reports or add a reading above.</p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {charts.map((c) => (
          <HealthChart key={c.key} title={c.title} unit={c.unit} series={c.series} />
        ))}
      </div>
    </div>
  );
}
