import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) },
  },
  oxc: {
    jsx: { runtime: "automatic", importSource: "react" },
  },
  test: {
    environment: "jsdom",
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    setupFiles: ["./tests/unit/setup.ts"],
    css: false,
    coverage: {
      provider: "v8",
      include: ["components/**", "lib/**"],
      reporter: ["text", "html"],
    },
  },
});
