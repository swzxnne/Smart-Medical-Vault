"use client";

import { useEffect, useState } from "react";
import { useApi } from "@/lib/api";
import type { Catalog } from "@/lib/types";

let cached: Catalog | null = null;

/** Metric metadata from the API; fetched once per page load. */
export function useCatalog(): Catalog | null {
  const api = useApi();
  const [catalog, setCatalog] = useState<Catalog | null>(cached);
  useEffect(() => {
    if (cached) return;
    api<Catalog>("/health/catalog").then((c) => {
      cached = c;
      setCatalog(c);
    });
  }, [api]);
  return catalog;
}
