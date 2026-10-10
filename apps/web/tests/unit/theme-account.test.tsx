import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeToggle } from "../../components/ThemeToggle";
import { keys } from "../../lib/api/hooks";
import type { Me } from "../../lib/api/types";
import { useAccountTheme } from "../../lib/theme-account";

function me(theme: Me["user"]["theme"]): Me {
  return {
    user: {
      id: "u1",
      display_name: "Pranav",
      email: null,
      phone: null,
      theme,
      timezone: null,
      notify_email: false,
      share_by_default: false,
    },
    houses: [],
  };
}

describe("theme preference saved to the account (PRD §14B ThemeToggle)", () => {
  let requests: { url: string; method: string; body: unknown }[];
  beforeEach(() => {
    document.cookie = "kt_csrf=test-token";
    requests = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        const body = init.body ? JSON.parse(String(init.body)) : undefined;
        requests.push({ url: String(url), method: String(init.method), body });
        return new Response(JSON.stringify({ ...me("dark").user, ...(body as object) }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("PWA-10 signed in: the toggle applies at once, saves on this device and PATCHes /me {theme}", async () => {
    const client = new QueryClient();
    client.setQueryData(keys.me, me("light"));
    render(
      <QueryClientProvider client={client}>
        <ThemeToggle />
      </QueryClientProvider>,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Switch to dark mode" }));
    });
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("kt-theme")).toBe("dark");
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({ url: "/api/v1/me", method: "PATCH", body: { theme: "dark" } });
    expect(client.getQueryData<Me>(keys.me)!.user.theme).toBe("dark");
  });

  it("PWA-10 signed out (or no provider): device only, no request", async () => {
    render(<ThemeToggle />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Switch to dark mode" }));
    });
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(requests).toHaveLength(0);
  });

  it("PWA-10 the account's theme is applied when /me loads on another device", () => {
    window.localStorage.setItem("kt-theme", "light");
    const wrapper = ({ children }: { children: ReactNode }) => <>{children}</>;
    const { rerender } = renderHook(({ theme }) => useAccountTheme(theme), {
      initialProps: { theme: undefined as Me["user"]["theme"] | undefined },
      wrapper,
    });
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
    rerender({ theme: "dark" });
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("kt-theme")).toBe("dark");
    rerender({ theme: "system" });
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
    expect(window.localStorage.getItem("kt-theme")).toBeNull();
  });
});
