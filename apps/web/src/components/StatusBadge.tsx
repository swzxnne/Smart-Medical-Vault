import type { DocumentStatus } from "@/lib/types";

const STYLES: Record<DocumentStatus, { label: string; className: string }> = {
  queued: { label: "Queued", className: "bg-slate-100 text-slate-600" },
  parsing: { label: "Reading text…", className: "bg-amber-100 text-amber-800" },
  indexing: { label: "Indexing…", className: "bg-amber-100 text-amber-800" },
  summarizing: { label: "Summarizing…", className: "bg-amber-100 text-amber-800" },
  ready: { label: "Ready", className: "bg-teal-100 text-teal-800" },
  failed: { label: "Failed", className: "bg-red-100 text-red-700" },
  uploaded: { label: "Not processed", className: "bg-slate-100 text-slate-600" },
};

export function StatusBadge({ status }: { status: DocumentStatus }) {
  const style = STYLES[status] ?? STYLES.queued;
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${style.className}`}>
      {style.label}
    </span>
  );
}
