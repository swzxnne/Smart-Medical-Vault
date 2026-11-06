import type { Flag } from "@/lib/types";

// Status colors are reserved for state and always paired with an icon + label.
const STATUS = {
  high: { icon: "▲", label: "High", color: "#d03b3b" },
  low: { icon: "▼", label: "Low", color: "#d03b3b" },
  normal: { icon: "●", label: "In range", color: "#0ca30c" },
} as const;

export function FlagBadge({ flag }: { flag: Flag | null }) {
  if (!flag) return null;
  const s = STATUS[flag];
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-700">
      <span aria-hidden style={{ color: s.color }}>{s.icon}</span>
      {s.label}
    </span>
  );
}
