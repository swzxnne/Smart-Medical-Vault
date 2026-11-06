"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function GlobalSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");

  return (
    <form
      role="search"
      className="w-full max-w-md"
      onSubmit={(e) => {
        e.preventDefault();
        const q = query.trim();
        if (q) router.push(`/search?q=${encodeURIComponent(q)}`);
      }}
    >
      <input
        type="search"
        className="input"
        placeholder="Search reports or ask a question…"
        aria-label="Search your documents"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
    </form>
  );
}
