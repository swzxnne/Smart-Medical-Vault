"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AskThread } from "@/components/AskPanel";
import { Highlighted } from "@/components/Highlighted";
import { useApi } from "@/lib/api";
import { useAsk } from "@/lib/ask";
import { formatDate, formatDocType } from "@/lib/format";
import type { Profile, SearchHit, SearchResponse } from "@/lib/types";

function groupByDocument(hits: SearchHit[]) {
  const groups = new Map<string, SearchHit[]>();
  for (const hit of hits) groups.set(hit.document_id, [...(groups.get(hit.document_id) ?? []), hit]);
  return [...groups.values()];
}

function SearchView() {
  const api = useApi();
  const router = useRouter();
  const params = useSearchParams();
  const q = params.get("q") ?? "";
  const profileId = params.get("profile_id") ?? "";

  const [draft, setDraft] = useState(q);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { turns, ask, retry, reset: resetAsk } = useAsk();

  useEffect(() => setDraft(q), [q]);

  useEffect(() => {
    api<Profile[]>("/profiles").then(setProfiles).catch(() => setProfiles([]));
  }, [api]);

  // Search runs on submit (URL change) only: the free embedding tier allows ~3 requests/minute.
  useEffect(() => {
    resetAsk();
    if (!q) {
      setResponse(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    const search = new URLSearchParams({ q, limit: "20" });
    if (profileId) search.set("profile_id", profileId);
    api<SearchResponse>(`/search?${search}`)
      .then((r) => !cancelled && setResponse(r))
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [api, q, profileId, resetAsk]);

  function navigate(nextQ: string, nextProfile: string) {
    const search = new URLSearchParams();
    if (nextQ) search.set("q", nextQ);
    if (nextProfile) search.set("profile_id", nextProfile);
    router.push(`/search?${search}`);
  }

  const groups = response ? groupByDocument(response.results) : [];

  return (
    <div className="space-y-6">
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          navigate(draft.trim(), profileId);
        }}
      >
        <input
          type="search"
          className="input min-w-0 flex-1"
          placeholder='e.g. "HbA1c", "What was the BP in 2024?"'
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          autoFocus
        />
        <select
          className="input w-auto"
          aria-label="Profile"
          value={profileId}
          onChange={(e) => navigate(q, e.target.value)}
        >
          <option value="">All profiles</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <button type="submit" className="btn-primary" disabled={!draft.trim()}>Search</button>
      </form>

      {q && (
        turns.length === 0 ? (
          <button className="btn-secondary border-teal-300 text-teal-800" onClick={() => ask(q, profileId)}>
            ✦ Ask AI: “{q}”
          </button>
        ) : (
          <AskThread turns={turns} onFollowUp={(f) => ask(f, profileId)} onRetry={() => retry(profileId)} />
        )
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}
      {loading && <p className="text-sm text-slate-500">Searching…</p>}
      {response?.mode === "keyword" && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
          Semantic search is rate-limited right now, so these are keyword matches only. Try again in a minute for
          better results.
        </p>
      )}

      {response && !loading && (
        <section className="space-y-3">
          <h2 className="text-sm text-slate-500">
            {response.results.length === 0
              ? `No matches for “${response.query}”.`
              : `${groups.length} document${groups.length === 1 ? "" : "s"} matching “${response.query}”`}
          </h2>
          {groups.map((hits) => {
            const first = hits[0];
            return (
              <article key={first.document_id} className="card space-y-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <Link href={`/documents/${first.document_id}`} className="font-medium hover:text-teal-700 hover:underline">
                    {first.document_name}
                  </Link>
                  <span className="text-xs text-slate-500">
                    {first.profile_name} · {formatDocType(first.doc_type)}
                    {first.doc_date && ` · ${formatDate(first.doc_date)}`}
                  </span>
                </div>
                {hits.map((hit) => (
                  <Link
                    key={hit.chunk_id}
                    href={`/documents/${hit.document_id}#page-${hit.page_no}`}
                    className="block rounded-md bg-slate-50 p-3 text-sm text-slate-700 hover:bg-slate-100"
                  >
                    <span className="mr-2 text-xs font-medium text-teal-700">p.{hit.page_no}</span>
                    <span className="whitespace-pre-line">
                      <Highlighted text={hit.snippet} />
                    </span>
                  </Link>
                ))}
              </article>
            );
          })}
        </section>
      )}

      {!q && <p className="text-sm text-slate-500">Search across every profile's reports, or ask a question in plain English.</p>}
    </div>
  );
}

export default function SearchPage() {
  // useSearchParams needs a Suspense boundary in the App Router.
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Loading…</p>}>
      <SearchView />
    </Suspense>
  );
}
