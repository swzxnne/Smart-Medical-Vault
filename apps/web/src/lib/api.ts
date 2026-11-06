"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg: string }) => d.msg).join("; ");
  } catch {
    // Non-JSON error body; fall through.
  }
  return res.statusText || `Request failed (${res.status})`;
}

/**
 * The Clerk session token, waiting briefly if Clerk isn't ready yet. Right after sign-in (and on a
 * cold page load) getToken() resolves to null for a moment; every page using the API is behind
 * sign-in, so null here means "not ready", not "signed out".
 */
export async function sessionToken(getToken: () => Promise<string | null>): Promise<string | null> {
  for (let attempt = 0; attempt < 50; attempt++) {
    const token = await getToken();
    if (token) return token;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return null;
}

/** Returns a fetch wrapper that calls the API with the current Clerk session token. */
export function useApi() {
  const { getToken } = useAuth();

  return useCallback(
    async <T>(path: string, init: RequestInit = {}): Promise<T> => {
      const token = await sessionToken(getToken);
      const res = await fetch(`${API_URL}${path}`, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...init.headers,
        },
      });
      if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
      return (res.status === 204 ? undefined : await res.json()) as T;
    },
    [getToken],
  );
}
