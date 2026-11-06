"use client";

import Link from "next/link";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FlagBadge } from "@/components/FlagBadge";
import { ReviewBadge } from "@/components/ReviewBadge";
import { useApi } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { formatValue, sourceHref, splitCitationLists } from "@/lib/health";
import type { HealthOverview, MetricSnapshot } from "@/lib/types";

function linkDocCitations(md: string, overview: HealthOverview): string {
  const byNumber = new Map((overview.summary?.citations ?? []).map((c) => [c.number, c]));
  return splitCitationLists(md, "D").replace(/\[D(\d+)\]/g, (match, n) => {
    const c = byNumber.get(Number(n));
    return c ? `[D${n}](/documents/${c.document_id})` : match;
  });
}

function MetricTile({ metric }: { metric: MetricSnapshot }) {
  const { latest, previous } = metric;
  const delta = previous?.value_num != null && latest.value_num != null ? latest.value_num - previous.value_num : null;
  const href = sourceHref(latest.document_id, latest.page_no);
  const body = (
    <>
      <p className="text-xs text-slate-500">{metric.name}</p>
      <p className="mt-1 text-xl font-semibold text-slate-900">{formatValue(latest)}</p>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
        {latest.observed_at && formatDate(latest.observed_at)}
        <FlagBadge flag={latest.flag} />
        {!latest.verified && <ReviewBadge note={latest.review_note} compact />}
      </p>
      {previous && delta !== null && (
        <p className="mt-1 text-xs text-slate-500">
          {delta === 0 ? "No change" : `${delta > 0 ? "+" : "−"}${Number(Math.abs(delta).toFixed(2))}`} since{" "}
          {previous.observed_at ? formatDate(previous.observed_at) : "previous"}
        </p>
      )}
    </>
  );
  return href ? (
    <Link href={href} className="card block p-4 transition hover:border-teal-500">{body}</Link>
  ) : (
    <div className="card p-4">{body}</div>
  );
}

export function ProfileOverview({
  profileId,
  overview,
  onChanged,
}: {
  profileId: string;
  overview: HealthOverview;
  onChanged: () => void;
}) {
  const api = useApi();
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    try {
      await action();
      setNotice(message);
      onChanged();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const empty =
    !overview.summary && !overview.summary_pending && overview.metrics.length === 0 && overview.medications.length === 0;

  return (
    <div className="space-y-6">
      {overview.outdated_documents > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md bg-teal-50 px-4 py-3 text-sm text-teal-900">
          <span>
            {overview.outdated_documents} document{overview.outdated_documents === 1 ? " was" : "s were"} processed
            before measurements were extracted.
          </span>
          <button
            className="btn-primary"
            disabled={busy}
            onClick={() =>
              run(
                () => api(`/documents/reprocess-outdated?profile_id=${profileId}`, { method: "POST" }),
                "Re-analysing. Values appear as each document finishes.",
              )
            }
          >
            Re-analyse
          </button>
        </div>
      )}
      {notice && <p className="text-sm text-slate-600">{notice}</p>}

      {overview.unverified_count > 0 && (
        <p className="rounded-md bg-amber-50 px-4 py-3 text-sm text-amber-900">
          {overview.unverified_count} value{overview.unverified_count === 1 ? " needs" : "s need"} review: they were
          read from hard-to-read scans or found away from their test name. Open the document (tiles marked
          &ldquo;Check&rdquo;) and confirm or correct them.
        </p>
      )}

      {overview.summary_pending && (
        <p className="card text-sm text-slate-600">
          <span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-teal-600" aria-hidden />
          Updating the health summary with the latest changes…
        </p>
      )}

      {empty && (
        <p className="card text-sm text-slate-600">
          Upload reports in the Documents tab. Measurements, medications and a health summary appear here once
          they&apos;re processed.
        </p>
      )}

      {overview.summary && (
        <section className="card space-y-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-semibold">Health summary</h2>
            <button
              className="text-xs font-medium text-teal-700 hover:underline disabled:opacity-50"
              disabled={busy}
              onClick={() => run(() => api(`/profiles/${profileId}/summary/refresh`, { method: "POST" }), "")}
            >
              Refresh
            </button>
          </div>
          <div className="markdown text-sm leading-relaxed text-slate-700">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ href, children }) => (
                  <Link href={href ?? "#"} className="rounded bg-teal-50 px-1 text-xs font-medium text-teal-700 no-underline hover:bg-teal-100">
                    {children}
                  </Link>
                ),
              }}
            >
              {linkDocCitations(overview.summary.content_md, overview)}
            </ReactMarkdown>
          </div>
          <p className="text-xs text-slate-400">
            Written by {overview.summary.model} from this profile&apos;s documents · updated{" "}
            {formatDate(overview.summary.created_at)}. Informational only, not medical advice.
          </p>
        </section>
      )}

      {overview.metrics.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-semibold">Latest measurements</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {overview.metrics.map((m) => (
              <MetricTile key={m.code} metric={m} />
            ))}
          </div>
        </section>
      )}

      {(overview.medications.length > 0 || overview.conditions.length > 0) && (
        <div className="grid gap-4 md:grid-cols-2">
          <section className="card">
            <h2 className="mb-3 font-semibold">Medications</h2>
            {overview.medications.length === 0 ? (
              <p className="text-sm text-slate-500">None found in documents.</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {overview.medications.map((m) => (
                  <li key={m.id}>
                    <p className="font-medium">{m.name}</p>
                    <p className="text-xs text-slate-500">
                      {[m.dose, m.frequency, m.duration].filter(Boolean).join(" · ")}
                      {m.prescribed_on && ` · prescribed ${formatDate(m.prescribed_on)}`} ·{" "}
                      <Link className="text-teal-700 hover:underline" href={sourceHref(m.document_id, m.page_no)!}>source</Link>
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="card">
            <h2 className="mb-3 font-semibold">Conditions & allergies</h2>
            {overview.conditions.length === 0 ? (
              <p className="text-sm text-slate-500">None found in documents.</p>
            ) : (
              <ul className="space-y-2 text-sm">
                {overview.conditions.map((c) => (
                  <li key={c.id} className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-medium">
                      {c.name}
                      {c.kind === "allergy" && <span className="ml-2 text-xs font-normal text-slate-500">allergy</span>}
                    </span>
                    <span className="text-xs text-slate-500">
                      {c.noted_on && formatDate(c.noted_on)} ·{" "}
                      <Link className="text-teal-700 hover:underline" href={sourceHref(c.document_id, c.page_no)!}>source</Link>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
