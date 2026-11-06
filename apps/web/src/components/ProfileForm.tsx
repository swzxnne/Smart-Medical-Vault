"use client";

import { useState } from "react";
import { BLOOD_GROUPS, type Profile, type ProfileInput } from "@/lib/types";

interface Props {
  initial?: Profile;
  submitLabel: string;
  onSubmit: (values: ProfileInput) => Promise<void>;
  onCancel?: () => void;
}

export function ProfileForm({ initial, submitLabel, onSubmit, onCancel }: Props) {
  const [name, setName] = useState(initial?.name ?? "");
  const [relation, setRelation] = useState(initial?.relation ?? "");
  const [dob, setDob] = useState(initial?.dob ?? "");
  const [sex, setSex] = useState(initial?.sex ?? "");
  const [bloodGroup, setBloodGroup] = useState(initial?.blood_group ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await onSubmit({
        name: name.trim(),
        relation: relation.trim() || null,
        dob: dob || null,
        sex: (sex || null) as ProfileInput["sex"],
        blood_group: (bloodGroup || null) as ProfileInput["blood_group"],
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="grid gap-4 sm:grid-cols-2">
      <div>
        <label className="label" htmlFor="name">Name *</label>
        <input id="name" className="input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="relation">Relation</label>
        <input id="relation" className="input" maxLength={40} placeholder="self, mother, son…" value={relation} onChange={(e) => setRelation(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="dob">Date of birth</label>
        <input id="dob" type="date" className="input" value={dob} onChange={(e) => setDob(e.target.value)} />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="label" htmlFor="sex">Sex</label>
          <select id="sex" className="input" value={sex} onChange={(e) => setSex(e.target.value)}>
            <option value="">—</option>
            <option value="female">Female</option>
            <option value="male">Male</option>
            <option value="other">Other</option>
          </select>
        </div>
        <div>
          <label className="label" htmlFor="blood_group">Blood group</label>
          <select id="blood_group" className="input" value={bloodGroup} onChange={(e) => setBloodGroup(e.target.value)}>
            <option value="">—</option>
            {BLOOD_GROUPS.map((g) => (
              <option key={g} value={g}>{g}</option>
            ))}
          </select>
        </div>
      </div>
      {error && <p className="text-sm text-red-600 sm:col-span-2">{error}</p>}
      <div className="flex gap-2 sm:col-span-2">
        <button type="submit" className="btn-primary" disabled={saving || !name.trim()}>
          {saving ? "Saving…" : submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="btn-secondary" onClick={onCancel}>Cancel</button>
        )}
      </div>
    </form>
  );
}
