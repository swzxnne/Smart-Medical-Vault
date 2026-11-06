"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ProfileForm } from "@/components/ProfileForm";
import { useApi } from "@/lib/api";
import { ageFrom } from "@/lib/format";
import type { Profile, ProfileInput } from "@/lib/types";

export default function ProfilesPage() {
  const api = useApi();
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    api<Profile[]>("/profiles")
      .then(setProfiles)
      .catch((err) => setError(err.message));
  }, [api]);

  async function create(values: ProfileInput) {
    const profile = await api<Profile>("/profiles", { method: "POST", body: JSON.stringify(values) });
    setProfiles((prev) => [...(prev ?? []), profile]);
    setAdding(false);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Profiles</h1>
        {!adding && <button className="btn-primary" onClick={() => setAdding(true)}>Add profile</button>}
      </div>

      {adding && (
        <div className="card">
          <h2 className="mb-4 font-medium">New profile</h2>
          <ProfileForm submitLabel="Create profile" onSubmit={create} onCancel={() => setAdding(false)} />
        </div>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}
      {profiles === null && !error && <p className="text-sm text-slate-500">Loading…</p>}
      {profiles?.length === 0 && !adding && (
        <div className="card text-center text-slate-600">
          Create a profile for yourself or a family member to start uploading reports.
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {profiles?.map((p) => {
          const age = ageFrom(p.dob);
          const details = [p.relation, age !== null && `${age} yrs`, p.sex, p.blood_group].filter(Boolean);
          return (
            <Link key={p.id} href={`/profiles/${p.id}`} className="card transition hover:border-teal-500">
              <p className="text-lg font-medium">{p.name}</p>
              <p className="mt-1 text-sm text-slate-500 capitalize">{details.join(" · ") || "No details"}</p>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
