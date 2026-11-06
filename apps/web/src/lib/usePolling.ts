"use client";

import { useEffect, useRef } from "react";

/** Calls `callback` every `intervalMs` while `active` is true. */
export function usePolling(callback: () => void, active: boolean, intervalMs = 3000) {
  const saved = useRef(callback);
  saved.current = callback;

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => saved.current(), intervalMs);
    return () => clearInterval(id);
  }, [active, intervalMs]);
}
