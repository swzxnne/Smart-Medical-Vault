"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DocumentFactsPanel } from "@/components/DocumentFactsPanel";
import { StatusBadge } from "@/components/StatusBadge";
import { ApiError, useApi } from "@/lib/api";
import { formatBytes, formatDate, formatDocType } from "@/lib/format";
import { splitCitationLists } from "@/lib/health";
import {
  type DocumentFacts,
  type DocumentPage,
  type DocumentSummary,
  IN_PROGRESS,
  type MedicalDocument,
} from "@/lib/types";
import { usePolling } from "@/lib/usePolling";

const LOW_CONFIDENCE = 0.75;

/** Turn "[p.2]" citations into links to that page's extracted text below. */
function linkCitations(md: string): string {
  return splitCitationLists(md, "p.").replace(/\[p\.(\d+)\]/g, "[p.$1](#page-$1)");
}

export default function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const [doc, setDoc] = useState<MedicalDocument | null>(null);
  const [summary, setSummary] = useState<DocumentSummary | null>(null);
  const [pages, setPages] = useState<DocumentPage[]>([]);
  const [extracted, setExtracted] = useState<DocumentFacts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reprocessing, setReprocessing] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api<MedicalDocument>(`/documents/${id}`);
      setDoc(d);
      setPages(await api<DocumentPage[]>(`/documents/${id}/pages`));
      setExtracted(await api<DocumentFacts>(`/documents/${id}/facts`));
      setSummary(
        await api<DocumentSummary>(`/documents/${id}/summary`).catch((err) => {
          if (err instanceof ApiError && err.status === 404) return null;
          throw err;
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError && err.status === 404 ? "Document not found" : String(err));
    }
  }, [api, id]);

  useEffect(() => {
    load();
  }, [load]);

  const hasPages = pages.length > 0;
  useEffect(() => {
    if (!hasPages || !window.location.hash) return;
    document.getElementById(window.location.hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [hasPages]);
  usePolling(load, !!doc && IN_PROGRESS.has(doc.status));

  async function openOriginal() {
    const tab = window.open("", "_blank");
    try {
      const { url } = await api<{ url: string }>(`/documents/${id}/file`);
      if (tab) tab.location.href = url;
      else window.location.href = url;
    } catch (err) {
      tab?.close();
      setActionError(err instanceof Error ? err.message : "Could not open file");
    }
  }

  async function reprocess() {
    setReprocessing(true);
    setActionError(null);
    try {
      setDoc(await api<MedicalDocument>(`/documents/${id}/reprocess`, { method: "POST" }));
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not reprocess");
    } finally {
      setReprocessing(false);
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
  if (!doc) return <p className="text-sm text-slate-500">Loading…</p>;

  const busy = IN_PROGRESS.has(doc.status);
  const facts = [
    ["Type", formatDocType(doc.doc_type)],
    ["Date", doc.doc_date ? formatDate(doc.doc_date) : "—"],
    ["Provider", doc.provider ?? "—"],
    ["Patient on report", doc.patient_name ?? "—"],
    ["Pages", doc.page_count?.toString() ?? "—"],
    ["File", `${formatBytes(doc.size_bytes)} · uploaded ${formatDate(doc.created_at)}`],
  ];

  return (
    <div className="space-y-6">
      <Link href={`/profiles/${doc.profile_id}`} className="text-sm text-teal-700 hover:underline">
        ← Back to profile
      </Link>

      <section className="card space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="truncate text-xl font-semibold">{doc.original_name}</h1>
            <div className="mt-1">
              <StatusBadge status={doc.status} />
            </div>
          </div>
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={openOriginal}>View original</button>
            <button className="btn-secondary" onClick={reprocess} disabled={busy || reprocessing}>
              {reprocessing ? "Starting…" : "Reprocess"}
            </button>
          </div>
        </div>
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt className="text-slate-500">{label}</dt>
              <dd className="font-medium">{value}</dd>
            </div>
          ))}
        </dl>
        {doc.review_reason && (
          <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
            ⚠ {doc.review_reason}. Check that it was uploaded to the right profile.
          </p>
        )}
        {doc.status === "failed" && doc.error && (
          <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{doc.error}</p>
        )}
        {actionError && <p className="text-sm text-red-600">{actionError}</p>}
      </section>

      <section className="card">
        <h2 className="mb-3 text-lg font-semibold">AI summary</h2>
        {summary ? (
          <>
            <div className="markdown text-sm leading-relaxed text-slate-700">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) => (
                    <a
                      href={href}
                      className="rounded bg-teal-50 px-1 text-xs font-medium text-teal-700 no-underline hover:bg-teal-100"
                    >
                      {children}
                    </a>
                  ),
                }}
              >
                {linkCitations(summary.content_md)}
              </ReactMarkdown>
            </div>
            <p className="mt-4 text-xs text-slate-400">
              Generated by {summary.model}. Informational only, not medical advice. Always check against the original.
            </p>
          </>
        ) : (
          <p className="text-sm text-slate-500">
            {busy ? "The summary will appear here once processing finishes." : "No summary yet."}
          </p>
        )}
      </section>

      {extracted && <DocumentFactsPanel facts={extracted} onChanged={load} />}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Extracted text</h2>
        {pages.length === 0 && (
          <p className="text-sm text-slate-500">{busy ? "Reading the document…" : "No text extracted."}</p>
        )}
        {pages.map((page) => {
          const vision = page.engine === "gemini-vision";
          const low = !vision && page.ocr_confidence !== null && page.ocr_confidence < LOW_CONFIDENCE;
          return (
            <div key={page.page_no} id={`page-${page.page_no}`} className="card scroll-mt-4 target:ring-2 target:ring-teal-500">
              <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                <span className="font-medium">Page {page.page_no}</span>
                <span className="text-xs text-slate-500">
                  {vision ? "Re-read by AI" : page.engine === "tesseract" ? "OCR" : "Text layer"}
                  {page.ocr_confidence !== null && ` · OCR ${Math.round(page.ocr_confidence * 100)}% confidence`}
                </span>
                {vision && (
                  <span className="text-xs text-amber-700">
                    ⚠ The scan was hard to read, so AI transcribed it. Check values against the original.
                  </span>
                )}
                {low && <span className="text-xs text-amber-700">⚠ Low OCR confidence, so values may be misread</span>}
              </div>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 font-mono text-xs text-slate-700">
                {page.text}
              </pre>
            </div>
          );
        })}
      </section>
    </div>
  );
}
