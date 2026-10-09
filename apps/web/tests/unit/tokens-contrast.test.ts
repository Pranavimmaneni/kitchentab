import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Parses styles/tokens.css and checks WCAG 2.2 contrast for every text/background token pair
 * the UI uses, in both themes (PRD §13, §14; ADR-0005).
 */
// Vitest runs from apps/web.
const css = readFileSync(resolvePath(process.cwd(), "styles/tokens.css"), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

type Tokens = Record<string, string>;

function block(selectorPattern: RegExp): Tokens {
  const match = selectorPattern.exec(css);
  if (!match) throw new Error(`No block for ${selectorPattern}`);
  const start = css.indexOf("{", match.index) + 1;
  const end = css.indexOf("}", start);
  const tokens: Tokens = {};
  for (const decl of css.slice(start, end).split(";")) {
    const m = /^\s*(--[\w-]+)\s*:\s*([^;]+?)\s*$/.exec(decl);
    if (m) tokens[m[1]!] = m[2]!;
  }
  return tokens;
}

const common = block(/^:root\s*\{/m);
const light = { ...common, ...block(/^:root,\s*\[data-theme="light"\]\s*\{/m) };
const dark = { ...common, ...block(/^\[data-theme="dark"\]\s*\{/m) };
const mediaDark = block(/:root:not\(\[data-theme="light"\]\)\s*\{/);
const themes = { light, dark } as const;

function resolve(tokens: Tokens, name: string, depth = 0): string {
  const raw = tokens[name];
  if (raw === undefined) throw new Error(`Missing token ${name}`);
  const ref = /^var\((--[\w-]+)\)$/.exec(raw);
  if (ref) {
    if (depth > 5) throw new Error(`Token cycle at ${name}`);
    return resolve(tokens, ref[1]!, depth + 1);
  }
  if (!/^#[0-9a-f]{6}$/i.test(raw)) throw new Error(`${name} is not a hex color: ${raw}`);
  return raw.toLowerCase();
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  ) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const TEXT = 4.5;
const UI = 3;

describe("design token contrast", () => {
  it("A11Y-05 tokens match the PRD §14 table in both themes", () => {
    const expected = {
      light: {
        "--bg": "#eceae2",
        "--paper": "#fbfaf6",
        "--ink": "#121210",
        "--muted": "#5a5850",
        "--faint": "#c9c6bb",
        "--control-edge": "#5a5850",
        "--shadow": "#121210",
        "--accent": "#c6f432",
        "--accent-2": "#6a4cff",
        "--on-accent-2": "#ffffff",
        // PRD says #C23A1E (4.45:1 on --bg); darkened to the nearest passing shade.
        "--warn": "#be381d",
        "--good": "#3e6b00",
      },
      dark: {
        "--bg": "#0d0d0b",
        "--paper": "#1a1a17",
        "--ink": "#f1efe7",
        "--muted": "#a9a699",
        "--faint": "#3a3933",
        "--control-edge": "#a9a699",
        "--shadow": "#7b61ff",
        "--accent": "#c6f432",
        "--accent-2": "#c6f432",
        "--on-accent-2": "#121210",
        "--warn": "#ff7a5c",
        "--good": "#c6f432",
      },
    };
    for (const [theme, values] of Object.entries(expected)) {
      for (const [name, hex] of Object.entries(values)) {
        expect(resolve(themes[theme as keyof typeof themes], name), `${theme} ${name}`).toBe(hex);
      }
    }
  });

  it("A11Y-05 the system-dark block is identical to the explicit dark theme", () => {
    expect(mediaDark).toEqual(block(/^\[data-theme="dark"\]\s*\{/m));
  });

  for (const [theme, tokens] of Object.entries(themes)) {
    it(`A11Y-05 ${theme}: text tokens reach 4.5:1 on paper and bg`, () => {
      for (const text of ["--ink", "--muted", "--warn", "--good"]) {
        for (const surface of ["--paper", "--bg"]) {
          const r = ratio(resolve(tokens, text), resolve(tokens, surface));
          expect(r, `${theme} ${text} on ${surface} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(
            TEXT,
          );
        }
      }
    });

    it(`A11Y-05 ${theme}: button and accent text reach 4.5:1`, () => {
      const pairs: [string, string][] = [
        ["--on-accent-2", "--accent-2"],
        ["--on-accent", "--accent"],
        ["--on-till", "--till"],
        ["--bg", "--ink"],
        ["--on-accent", "--tag-fridge"],
        ["--on-accent", "--tag-freezer"],
        ["--on-accent", "--tag-pantry"],
      ];
      for (const [text, surface] of pairs) {
        const r = ratio(resolve(tokens, text), resolve(tokens, surface));
        expect(r, `${theme} ${text} on ${surface} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(TEXT);
      }
      expect(resolve(tokens, "--on-accent")).toBe("#121210");
    });

    it(`A11Y-05 ${theme}: member initials reach 4.5:1 on every member color`, () => {
      for (let i = 1; i <= 8; i++) {
        const fill = resolve(tokens, `--member-${i}`);
        const initial = resolve(tokens, `--on-member-${i}`);
        expect(initial, `member ${i}`).toBe(fill === "#6a4cff" ? "#ffffff" : "#121210");
        const r = ratio(initial, fill);
        expect(r, `member ${i} ${initial} on ${fill} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(
          TEXT,
        );
      }
    });

    it(`A11Y-05 ${theme}: --control-edge reaches 3:1 on paper and bg (WCAG 1.4.11)`, () => {
      for (const surface of ["--paper", "--bg"]) {
        const r = ratio(resolve(tokens, "--control-edge"), resolve(tokens, surface));
        expect(r, `${theme} control-edge on ${surface} = ${r.toFixed(2)}`).toBeGreaterThanOrEqual(
          UI,
        );
      }
      // --faint is decorative only and must never be the only edge of a control.
      expect(ratio(resolve(tokens, "--faint"), resolve(tokens, "--paper"))).toBeLessThan(UI);
    });
  }
});
