"use client";

import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useState } from "react";
import { FlagBadge } from "@/components/FlagBadge";
import { ReviewBadge } from "@/components/ReviewBadge";
import type { AskTurn } from "@/lib/ask";
import { formatDate } from "@/lib/format";
import { formatValue, splitCitationLists, sourceHref as pageHref } from "@/lib/health";
import type { AskSource } from "@/lib/types";

function sourceHref(source: AskSource) {
  return pageHref(source.document_id, source.page_no) ?? "#";
}

/** Turn "[S2]" into a link to that source's page; unknown numbers stay plain text. */
function linkCitations(text: string, sources: AskSource[]): string {
  const byNumber = new Map(sources.map((s) => [s.number, s]));
  return splitCitationLists(text, "S").replace(/\[S(\d+)\]/g, (match, n) => {
    const source = byNumber.get(Number(n));
    return source ? `[S${n}](${sourceHref(source)})` : match;
  });
}

export function AskThread({
  turns,
  onFollowUp,
  onRetry,
}: {
  turns: AskTurn[];
  onFollowUp: (question: string) => void;
  onRetry: () => void;
}) {
  const [draft, setDraft] = useState("");
  const last = turns[turns.length - 1];
  return (
    <section className="card space-y-6 border-teal-200">
      {turns.map((turn, i) => (
        <AskTurnView key={i} state={turn} onRetry={i === turns.length - 1 ? onRetry : undefined} first={i === 0} />
      ))}
      {last && last.status !== "streaming" && (
        <form
          className="flex gap-2 border-t border-slate-100 pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!draft.trim()) return;
            onFollowUp(draft.trim());
            setDraft("");
          }}
        >
          <input
            className="input"
            placeholder='Ask a follow-up, e.g. "and in 2023?" or "what about Ravi?"'
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button className="btn-primary" disabled={!draft.trim()}>Ask</button>
        </form>
      )}
    </section>
  );
}

function AskTurnView({ state, onRetry, first }: { state: AskTurn; onRetry?: () => void; first: boolean }) {
  const cited = new Set(state.cited);
  const shownSources = state.status === "done" ? state.sources.filter((s) => cited.has(s.number)) : state.sources;

  const searchedFor =
    state.standalone && state.standalone.trim().toLowerCase() !== state.question.trim().toLowerCase()
      ? state.standalone
      : null;
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          {first ? (
            <h2 className="font-semibold text-teal-800">AI answer</h2>
          ) : (
            <p className="font-medium text-slate-800">{state.question}</p>
          )}
          {searchedFor && <p className="text-xs text-slate-500">Searched for: {searchedFor}</p>}
        </div>
        {state.status === "streaming" && <span className="text-xs text-slate-500">Thinking…</span>}
      </div>

      {state.answer && (
        <div className="markdown text-sm leading-relaxed text-slate-700">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => (
                <Link
                  href={href ?? "#"}
                  className="rounded bg-teal-50 px-1 text-xs font-medium text-teal-700 no-underline hover:bg-teal-100"
                >
                  {children}
                </Link>
              ),
            }}
          >
            {linkCitations(state.answer, state.sources)}
          </ReactMarkdown>
        </div>
      )}
      {state.status === "streaming" && !state.answer && (
        <p className="text-sm text-slate-500">
          {state.sources.length ? `Reading ${state.sources.length} relevant passages…` : "Searching your documents…"}
        </p>
      )}
      {state.status === "error" && (
        <div className="flex items-center gap-3 text-sm text-red-600">
          <span>{state.error}</span>
          {onRetry && <button className="btn-secondary" onClick={onRetry}>Try again</button>}
        </div>
      )}

      {state.observations.length > 0 && (
        <div className="overflow-x-auto">
          <h3 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">Readings from your records</h3>
          <table className="w-full text-sm tabular-nums">
            <tbody>
              {state.observations.map((o, i) => (
                <tr key={i} className="border-b border-slate-100">
                  <td className="py-1 pr-3 text-slate-500">{o.observed_at ? formatDate(o.observed_at) : "—"}</td>
                  <td className="py-1 pr-3">{o.profile_name}</td>
                  <td className="py-1 pr-3">{o.name}</td>
                  <td className="py-1 pr-3 font-medium">{formatValue(o)}</td>
                  <td className="py-1 pr-3">
                    <FlagBadge flag={o.flag} /> {!o.verified && <ReviewBadge note="Not yet checked" compact />}
                  </td>
                  <td className="py-1 text-xs text-teal-700">S{o.source_number}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {shownSources.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
            {state.status === "done" ? "Sources cited" : "Sources"}
          </h3>
          <ol className="space-y-1 text-sm">
            {shownSources.map((s) => (
              <li key={s.number} className="flex gap-2">
                <span className="w-7 shrink-0 font-medium text-teal-700">S{s.number}</span>
                <Link href={sourceHref(s)} className="min-w-0 truncate hover:underline">
                  {s.profile_name} · {s.document_name}
                  {s.doc_date && ` · ${formatDate(s.doc_date)}`}
                  {s.page_no && ` · p.${s.page_no}`}
                  {s.kind === "measurements" && " · measurements"}
                </Link>
              </li>
            ))}
          </ol>
        </div>
      )}
      {state.status === "done" && (
        <p className="text-xs text-slate-400">
          Answered by {state.model ?? "—"} from your documents only. Informational, not medical advice.
        </p>
      )}
    </div>
  );
}
