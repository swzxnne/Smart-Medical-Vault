"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { todayLocal } from "@/lib/format";
import type { Catalog, Observation } from "@/lib/types";

export function AddReadingForm({
  profileId,
  catalog,
  onAdded,
}: {
  profileId: string;
  catalog: Catalog;
  onAdded: (observations: Observation[]) => void;
}) {
  const api = useApi();
  const [code, setCode] = useState("blood_pressure");
  const [value, setValue] = useState("");
  const [diastolic, setDiastolic] = useState("");
  const [date, setDate] = useState(todayLocal);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const metric = catalog.metrics.find((m) => m.code === code);
  const isBp = code === "blood_pressure";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const entries = isBp
        ? [["bp_systolic", value], ["bp_diastolic", diastolic]]
        : [[code, value]];
      const added: Observation[] = [];
      for (const [c, v] of entries) {
        added.push(
          await api<Observation>(`/profiles/${profileId}/observations`, {
            method: "POST",
            body: JSON.stringify({ code: c, value: Number(v), observed_at: date }),
          }),
        );
      }
      onAdded(added);
      setValue("");
      setDiastolic("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="card flex flex-wrap items-end gap-3">
      <div>
        <label className="label" htmlFor="metric">Add a reading</label>
        <select id="metric" className="input w-auto" value={code} onChange={(e) => setCode(e.target.value)}>
          <option value="blood_pressure">Blood pressure</option>
          {catalog.metrics
            .filter((m) => !m.code.startsWith("bp_"))
            .map((m) => (
              <option key={m.code} value={m.code}>{m.name}</option>
            ))}
        </select>
      </div>
      <div className="w-28">
        <label className="label" htmlFor="value">{isBp ? "Systolic" : `Value (${metric?.unit})`}</label>
        <input id="value" className="input" type="number" step="any" required value={value} onChange={(e) => setValue(e.target.value)} />
      </div>
      {isBp && (
        <div className="w-28">
          <label className="label" htmlFor="diastolic">Diastolic</label>
          <input id="diastolic" className="input" type="number" step="any" required value={diastolic} onChange={(e) => setDiastolic(e.target.value)} />
        </div>
      )}
      <div>
        <label className="label" htmlFor="date">Date</label>
        <input id="date" className="input" type="date" required max={todayLocal()} value={date} onChange={(e) => setDate(e.target.value)} />
      </div>
      <button className="btn-primary" disabled={saving}>{saving ? "Saving…" : "Add"}</button>
      {error && <p className="w-full text-sm text-red-600">{error}</p>}
    </form>
  );
}
