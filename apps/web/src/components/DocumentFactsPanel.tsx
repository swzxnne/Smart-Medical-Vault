"use client";

import { useState } from "react";
import { FlagBadge } from "@/components/FlagBadge";
import { ReviewBadge } from "@/components/ReviewBadge";
import { useApi } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { formatValue } from "@/lib/health";
import type { DocumentFacts, Observation } from "@/lib/types";

function ObservationRow({ obs, onChanged }: { obs: Observation; onChanged: () => void }) {
  const api = useApi();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(obs.value_num?.toString() ?? "");
  const [date, setDate] = useState(obs.observed_at ?? "");
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    try {
      await api(`/observations/${obs.id}`, {
        method: "PATCH",
        body: JSON.stringify({ value: value === "" ? null : Number(value), observed_at: date || null }),
      });
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  }

  async function confirm() {
    await api(`/observations/${obs.id}`, { method: "PATCH", body: JSON.stringify({ confirm: true }) });
    onChanged();
  }

  async function remove() {
    await api(`/observations/${obs.id}`, { method: "DELETE" });
    onChanged();
  }

  return (
    <tr className="border-b border-slate-100 align-top">
      <td className="py-2 pr-3">
        <p className="font-medium">{obs.name}</p>
        {obs.source === "corrected" && <p className="text-xs text-slate-500">Checked by you</p>}
        {!obs.verified && (
          <p className="mt-1 space-y-1">
            <ReviewBadge note={obs.review_note} />
            <span className="block text-xs text-amber-800">{obs.review_note}</span>
          </p>
        )}
      </td>
      <td className="py-2 pr-3 tabular-nums">
        {editing ? (
          <div className="space-y-1">
            <input className="input w-28" type="number" step="any" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Value" />
            <input className="input w-36" type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Date" />
            {error && <p className="text-xs text-red-600">{error}</p>}
          </div>
        ) : (
          <>
            <p>{formatValue(obs)}</p>
            {obs.code !== "other" && obs.value_text.trim() !== formatValue(obs) && (
              <p className="text-xs text-slate-500">printed: {obs.value_text}</p>
            )}
          </>
        )}
      </td>
      <td className="py-2 pr-3 text-xs text-slate-500">{obs.ref_text ?? "—"}</td>
      <td className="py-2 pr-3"><FlagBadge flag={obs.flag} /></td>
      <td className="py-2 pr-3 text-xs">
        {obs.observed_at ? formatDate(obs.observed_at) : "—"}
        {obs.page_no && (
          <a href={`#page-${obs.page_no}`} className="ml-2 text-teal-700 hover:underline">p.{obs.page_no}</a>
        )}
      </td>
      <td className="py-2 text-right whitespace-nowrap">
        {editing ? (
          <>
            <button className="text-xs font-medium text-teal-700 hover:underline" onClick={save}>Save</button>
            <button className="ml-3 text-xs text-slate-500 hover:underline" onClick={() => setEditing(false)}>Cancel</button>
          </>
        ) : (
          <>
            {!obs.verified && (
              <button className="mr-3 text-xs font-medium text-teal-700 hover:underline" onClick={confirm}>Confirm</button>
            )}
            <button className="text-xs font-medium text-teal-700 hover:underline" onClick={() => setEditing(true)}>Correct</button>
            <button className="ml-3 text-xs text-red-600 hover:underline" onClick={remove}>Remove</button>
          </>
        )}
      </td>
    </tr>
  );
}

export function DocumentFactsPanel({ facts, onChanged }: { facts: DocumentFacts; onChanged: () => void }) {
  const { observations, medications, conditions } = facts;
  if (!observations.length && !medications.length && !conditions.length) return null;

  return (
    <section className="card space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Extracted values</h2>
        <p className="text-xs text-slate-500">
          Read from the document and checked against its text. Values marked &ldquo;Needs review&rdquo; couldn&apos;t
          be fully checked: confirm or correct them against the original. Your changes are kept if the document is
          re-analysed.
        </p>
      </div>
      {observations.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                <th className="py-1.5 pr-3 font-medium">Test</th>
                <th className="py-1.5 pr-3 font-medium">Value</th>
                <th className="py-1.5 pr-3 font-medium">Reference</th>
                <th className="py-1.5 pr-3 font-medium">Flag</th>
                <th className="py-1.5 pr-3 font-medium">Date · page</th>
                <th className="py-1.5" />
              </tr>
            </thead>
            <tbody>
              {observations.map((o) => (
                <ObservationRow key={o.id} obs={o} onChanged={onChanged} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(medications.length > 0 || conditions.length > 0) && (
        <div className="grid gap-4 text-sm sm:grid-cols-2">
          {medications.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Medications</h3>
              <ul className="space-y-1">
                {medications.map((m) => (
                  <li key={m.id}>
                    <span className="font-medium">{m.name}</span>{" "}
                    <span className="text-slate-500">{[m.dose, m.frequency, m.duration].filter(Boolean).join(" · ")}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {conditions.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Conditions</h3>
              <ul className="space-y-1">
                {conditions.map((c) => (
                  <li key={c.id}>
                    {c.name}
                    {c.kind === "allergy" && <span className="ml-2 text-xs text-slate-500">allergy</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
