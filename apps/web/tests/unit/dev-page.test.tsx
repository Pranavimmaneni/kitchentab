import { afterEach, describe, expect, it, vi } from "vitest";
import DevComponentsPage, { dynamic } from "@/app/dev/components/page";

function notFoundDigest(fn: () => unknown): string | undefined {
  try {
    fn();
  } catch (error) {
    return (error as { digest?: string }).digest;
  }
  return undefined;
}

describe("/dev/components gate", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("reads the flag per request", () => {
    expect(dynamic).toBe("force-dynamic");
  });

  it("is a 404 in production without KT_DEV_COMPONENTS=1", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("KT_DEV_COMPONENTS", "");
    expect(notFoundDigest(() => DevComponentsPage())).toMatch(/404/);
    vi.stubEnv("KT_DEV_COMPONENTS", "true");
    expect(notFoundDigest(() => DevComponentsPage())).toMatch(/404/);
  });

  it("renders in production with KT_DEV_COMPONENTS=1 and always in development", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("KT_DEV_COMPONENTS", "1");
    expect(DevComponentsPage()).toBeTruthy();
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("KT_DEV_COMPONENTS", "");
    expect(DevComponentsPage()).toBeTruthy();
  });
});
