import type { Observation } from "@/lib/types";

/** Canonical value with unit, e.g. "138 mmHg"; free-text results fall back to what was printed. */
export function formatValue(obs: Pick<Observation, "value_num" | "value_text" | "unit" | "code">): string {
  if (obs.value_num === null || obs.code === "other") return [obs.value_text, obs.code === "other" ? obs.unit : null].filter(Boolean).join(" ");
  const rounded = Number(obs.value_num.toFixed(obs.value_num < 10 ? 2 : 1));
  return `${rounded} ${obs.unit ?? ""}`.trim();
}

/** Link to the page a fact came from, or null for manually entered readings. */
export function sourceHref(documentId: string | null, pageNo: number | null): string | null {
  if (!documentId) return null;
  return `/documents/${documentId}${pageNo ? `#page-${pageNo}` : ""}`;
}

/** "[D2, D3]" / "[p.1, p.2]" -> "[D2][D3]" so each citation becomes its own link. */
export function splitCitationLists(md: string, prefix: "D" | "p." | "S"): string {
  const escaped = prefix.replace(".", "\\.");
  const list = new RegExp(`\\[(${escaped}\\d+(?:\\s*,\\s*(?:${escaped})?\\d+)+)\\]`, "g");
  return md.replace(list, (_, inner: string) =>
    inner
      .split(",")
      .map((part) => `[${prefix}${part.trim().replace(prefix, "")}]`)
      .join(""),
  );
}
