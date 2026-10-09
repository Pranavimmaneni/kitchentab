"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { initTheme } from "@/lib/theme";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          // Reads only; writes never retry automatically (PRD §21).
          queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: true },
          mutations: { retry: false },
        },
      }),
  );
  useEffect(() => initTheme(), []);
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
