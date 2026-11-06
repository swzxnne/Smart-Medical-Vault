"use client";

import Link from "next/link";
import { useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { useApi } from "@/lib/api";
import { formatBytes, formatDate, formatDocType } from "@/lib/format";
import type { MedicalDocument } from "@/lib/types";

interface Props {
  documents: MedicalDocument[];
  onDeleted: (id: string) => void;
}

export function DocumentList({ documents, onDeleted }: Props) {
  const api = useApi();
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function view(doc: MedicalDocument) {
    // Open the tab synchronously so popup blockers allow it, then point it at the signed URL.
    const tab = window.open("", "_blank");
    try {
      const { url } = await api<{ url: string }>(`/documents/${doc.id}/file`);
      if (tab) tab.location.href = url;
      else window.location.href = url;
    } catch (err) {
      tab?.close();
      setError(err instanceof Error ? err.message : "Could not open file");
    }
  }

  async function remove(doc: MedicalDocument) {
    setBusyId(doc.id);
    setError(null);
    try {
      await api(`/documents/${doc.id}`, { method: "DELETE" });
      onDeleted(doc.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete");
    } finally {
      setBusyId(null);
      setConfirmingId(null);
    }
  }

  if (documents.length === 0) {
    return <p className="text-sm text-slate-500">No documents yet. Upload the first report above.</p>;
  }

  return (
    <div>
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-white">
        {documents.map((doc) => (
          <li key={doc.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0 space-y-1">
              <Link href={`/documents/${doc.id}`} className="block truncate font-medium hover:text-teal-700 hover:underline">
                {doc.original_name}
              </Link>
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-500">
                <StatusBadge status={doc.status} />
                {doc.doc_type && <span>{formatDocType(doc.doc_type)}</span>}
                {doc.doc_date && <span>· {formatDate(doc.doc_date)}</span>}
                {doc.provider && <span className="truncate">· {doc.provider}</span>}
                <span>
                  · {formatBytes(doc.size_bytes)} · uploaded {formatDate(doc.created_at)}
                </span>
              </p>
              {doc.review_reason && <p className="text-xs text-amber-700">⚠ {doc.review_reason}</p>}
              {doc.status === "failed" && doc.error && <p className="text-xs text-red-600">{doc.error}</p>}
            </div>
            <div className="flex gap-2">
              <button className="btn-secondary" onClick={() => view(doc)}>View</button>
              {confirmingId === doc.id ? (
                <>
                  <button className="btn-danger" disabled={busyId === doc.id} onClick={() => remove(doc)}>
                    {busyId === doc.id ? "Deleting…" : "Confirm delete"}
                  </button>
                  <button className="btn-secondary" onClick={() => setConfirmingId(null)}>Cancel</button>
                </>
              ) : (
                <button className="btn-secondary text-red-600" onClick={() => setConfirmingId(doc.id)}>Delete</button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
