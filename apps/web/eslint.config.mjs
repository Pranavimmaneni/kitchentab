import { createRequire } from "node:module";
import { dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// eslint-config-next 15 ships eslintrc-style configs. FlatCompat comes from ESLint's own
// dependency @eslint/eslintrc, resolved relative to eslint so no extra package is needed.
const require = createRequire(import.meta.url);
const eslintrcPath = createRequire(require.resolve("eslint/package.json")).resolve(
  "@eslint/eslintrc",
);
const { FlatCompat } = await import(pathToFileURL(eslintrcPath).href);

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "test-results/**",
      "playwright-report/**",
      "coverage/**",
      "next-env.d.ts",
    ],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
];

export default config;
