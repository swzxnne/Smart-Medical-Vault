"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useRef, useState } from "react";
import { API_URL, sessionToken } from "@/lib/api";
import type { AskObservation, AskSource } from "@/lib/types";

export type AskTurn = {
  question: string;
  status: "streaming" | "done" | "error";
  answer: string;
  sources: AskSource[];
  observations: AskObservation[];
  cited: number[];
  mode: "hybrid" | "keyword" | null;
  model: string | null;
  error: string | null;
  // What the server actually searched for (follow-ups are rewritten into standalone questions).
  standalone: string | null;
};

const MAX_HISTORY = 3;

/** A conversation with POST /ask, consuming its server-sent events (EventSource can't send auth headers). */
export function useAsk() {
  const { getToken } = useAuth();
  const [turns, setTurns] = useState<AskTurn[]>([]);
  const turnsRef = useRef<AskTurn[]>([]);
  turnsRef.current = turns;
  const abortRef = useRef<AbortController | null>(null);

  const run = useCallback(
    async (question: string, profileId: string | undefined, history: { question: string; answer: string }[]) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      const update = (patch: Partial<AskTurn> | ((t: AskTurn) => Partial<AskTurn>)) =>
        setTurns((prev) => {
          const last = prev[prev.length - 1];
          const next = typeof patch === "function" ? patch(last) : patch;
          return [...prev.slice(0, -1), { ...last, ...next }];
        });
      const fail = (error: string) => update({ status: "error", error });

      try {
        const token = await sessionToken(getToken);
        const res = await fetch(`${API_URL}/ask`, {
          method: "POST",
          signal: controller.signal,
          headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
          body: JSON.stringify({ question, profile_id: profileId || null, history }),
        });
        if (!res.ok || !res.body) return fail(`Request failed (${res.status})`);

        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;
          let boundary;
          while ((boundary = buffer.indexOf("\n\n")) !== -1) {
            handleEvent(buffer.slice(0, boundary));
            buffer = buffer.slice(boundary + 2);
          }
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") fail("Connection lost. Please try again.");
      }

      function handleEvent(block: string) {
        let event = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        const payload = JSON.parse(data || "{}");
        switch (event) {
          case "sources":
            return update({
              sources: payload.sources,
              observations: payload.observations ?? [],
              mode: payload.mode,
              standalone: payload.standalone_question ?? null,
            });
          case "delta":
            return update((t) => ({ answer: t.answer + payload.text }));
          case "done":
            return update({ status: "done", cited: payload.cited, model: payload.model });
          case "error":
            return update({ status: "error", error: payload.message });
        }
      }
    },
    [getToken],
  );

  /** Ask a new question; earlier answered turns are sent as context for follow-ups. */
  const ask = useCallback(
    (question: string, profileId?: string) => {
      const history = turnsRef.current
        .filter((t) => t.status === "done")
        .slice(-MAX_HISTORY)
        .map((t) => ({ question: t.question, answer: t.answer }));
      setTurns((prev) => [...prev, emptyTurn(question)]);
      return run(question, profileId, history);
    },
    [run],
  );

  /** Re-ask the last question (e.g. after "AI is busy"), with the same context. */
  const retry = useCallback(
    (profileId?: string) => {
      const all = turnsRef.current;
      const last = all[all.length - 1];
      if (!last) return;
      const history = all
        .slice(0, -1)
        .filter((t) => t.status === "done")
        .slice(-MAX_HISTORY)
        .map((t) => ({ question: t.question, answer: t.answer }));
      setTurns((prev) => [...prev.slice(0, -1), emptyTurn(last.question)]);
      return run(last.question, profileId, history);
    },
    [run],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setTurns([]);
  }, []);

  return { turns, ask, retry, reset };
}

function emptyTurn(question: string): AskTurn {
  return {
    question,
    status: "streaming",
    answer: "",
    sources: [],
    observations: [],
    cited: [],
    mode: null,
    model: null,
    error: null,
    standalone: null,
  };
}
