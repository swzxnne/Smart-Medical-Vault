/** Marks an extracted value a person should check (label not beside it, or read by AI from a poor scan). */
export function ReviewBadge({ note, compact = false }: { note: string | null; compact?: boolean }) {
  return (
    <span
      title={note ?? "Needs review"}
      className="inline-flex items-center gap-1 rounded bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-800"
    >
      <span aria-hidden>?</span>
      {compact ? "Check" : "Needs review"}
    </span>
  );
}
