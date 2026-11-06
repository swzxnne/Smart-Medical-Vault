"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { DocumentList } from "@/components/DocumentList";
import { DocumentUploader } from "@/components/DocumentUploader";
import { ProfileCharts } from "@/components/ProfileCharts";
import { ProfileForm } from "@/components/ProfileForm";
import { ProfileOverview } from "@/components/ProfileOverview";
import { ApiError, useApi } from "@/lib/api";
import { ageFrom } from "@/lib/format";
import { type HealthOverview, IN_PROGRESS, type MedicalDocument, type Profile, type ProfileInput } from "@/lib/types";
import { useCatalog } from "@/lib/useCatalog";
import { usePolling } from "@/lib/usePolling";

const TABS = [
  ["overview", "Overview"],
  ["charts", "Charts"],
  ["documents", "Documents"],
] as const;
type Tab = (typeof TABS)[number][0];

export default function ProfileDetailPage() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [documents, setDocuments] = useState<MedicalDocument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<HealthOverview | null>(null);
  const catalog = useCatalog();

  useEffect(() => {
    Promise.all([
      api<Profile>(`/profiles/${id}`),
      api<MedicalDocument[]>(`/documents?profile_id=${id}`),
    ])
      .then(([p, docs]) => {
        setProfile(p);
        setDocuments(docs);
      })
      .catch((err) => setError(err instanceof ApiError && err.status === 404 ? "Profile not found" : err.message));
  }, [api, id]);

  const loadOverview = useCallback(() => {
    api<HealthOverview>(`/profiles/${id}/health`)
      .then(setOverview)
      .catch(() => {
        // Keep the last overview on a transient failure.
      });
  }, [api, id]);
  useEffect(loadOverview, [loadOverview]);

  const refreshDocuments = useCallback(() => {
    api<MedicalDocument[]>(`/documents?profile_id=${id}`)
      .then(setDocuments)
      .catch(() => {
        // Transient polling failure; the next tick retries.
      });
    loadOverview();
  }, [api, id, loadOverview]);
  const processing = documents.some((d) => IN_PROGRESS.has(d.status));
  usePolling(refreshDocuments, processing);
  const wasProcessing = useRef(false);
  useEffect(() => {
    // The last poll can read the overview a moment before the final document's facts are
    // committed; fetch it once more now that every document is done.
    if (wasProcessing.current && !processing) loadOverview();
    wasProcessing.current = processing;
  }, [processing, loadOverview]);
  // The server says when a summary rewrite is scheduled or running; poll until it lands.
  usePolling(loadOverview, !processing && !!overview?.summary_pending, 5_000);

  async function save(values: ProfileInput) {
    const updated = await api<Profile>(`/profiles/${id}`, { method: "PATCH", body: JSON.stringify(values) });
    setProfile(updated);
    setEditing(false);
  }

  async function deleteProfile() {
    setDeleting(true);
    try {
      await api(`/profiles/${id}`, { method: "DELETE" });
      router.push("/profiles");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete profile");
      setDeleting(false);
    }
  }

  if (error) {
    return (
      <div className="space-y-4">
        <p className="text-red-600">{error}</p>
        <Link href="/profiles" className="btn-secondary">Back to profiles</Link>
      </div>
    );
  }
  if (!profile) return <p className="text-sm text-slate-500">Loading…</p>;

  const age = ageFrom(profile.dob);
  const details = [profile.relation, age !== null && `${age} yrs`, profile.sex, profile.blood_group].filter(Boolean);

  return (
    <div className="space-y-6">
      <Link href="/profiles" className="text-sm text-teal-700 hover:underline">← All profiles</Link>

      <section className="card">
        {editing ? (
          <ProfileForm initial={profile} submitLabel="Save" onSubmit={save} onCancel={() => setEditing(false)} />
        ) : (
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold">{profile.name}</h1>
              <p className="mt-1 text-sm text-slate-500 capitalize">{details.join(" · ") || "No details"}</p>
            </div>
            <div className="flex gap-2">
              <Link href={`/search?profile_id=${profile.id}`} className="btn-secondary">Search {profile.name}&apos;s records</Link>
              <button className="btn-secondary" onClick={() => setEditing(true)}>Edit</button>
              {confirmDelete ? (
                <>
                  <button className="btn-danger" disabled={deleting} onClick={deleteProfile}>
                    {deleting ? "Deleting…" : "Delete profile and all documents"}
                  </button>
                  <button className="btn-secondary" onClick={() => setConfirmDelete(false)}>Cancel</button>
                </>
              ) : (
                <button className="btn-secondary text-red-600" onClick={() => setConfirmDelete(true)}>Delete</button>
              )}
            </div>
          </div>
        )}
      </section>

      <nav className="flex gap-1 border-b border-slate-200" aria-label="Profile sections">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            aria-current={tab === key ? "page" : undefined}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
              tab === key ? "border-teal-700 text-teal-800" : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            {label}
            {key === "documents" && documents.length > 0 && (
              <span className="ml-1.5 text-xs text-slate-400">{documents.length}</span>
            )}
          </button>
        ))}
      </nav>

      {tab === "overview" &&
        (overview ? (
          <ProfileOverview profileId={profile.id} overview={overview} onChanged={refreshDocuments} />
        ) : (
          <p className="text-sm text-slate-500">Loading…</p>
        ))}

      {tab === "charts" &&
        (catalog ? <ProfileCharts profileId={profile.id} catalog={catalog} /> : <p className="text-sm text-slate-500">Loading…</p>)}

      {tab === "documents" && (
        <section className="space-y-4">
          <DocumentUploader profileId={profile.id} onUploaded={(doc) => setDocuments((prev) => [doc, ...prev])} />
          <DocumentList documents={documents} onDeleted={(docId) => setDocuments((prev) => prev.filter((d) => d.id !== docId))} />
        </section>
      )}
    </div>
  );
}
