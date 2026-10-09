# KitchenTab design mockups

These sixteen files are the approved mockups (390 px wide phone frames; long screens are taller frames instead of inner scroll areas, so real pages simply scroll). They are the visual
source of truth together with PRD §14, §14A and §14B. Dark mode is the same files with
the `mode` prop set to `dark`; every screen also has a working theme toggle.

| File | Screen | Route |
| --- | --- | --- |
| Signin.dc.html | A · Start a house | `/start` |
| Verify.dc.html | B · Enter code | `/verify` |
| House.dc.html | C · Who lives here | `/houses/new` |
| Join.dc.html | D · Join from a link | `/h/{token}`, `/b/{token}` |
| Share.dc.html | E · Share after send | `/bills/{id}/share` |
| Main.dc.html | 1 · Tabs (was Home) | `/tabs` |
| Scan.dc.html | 2 · Check the receipt | `/bills/{id}/review` |
| Split.dc.html | 3 · Who's eating what | `/bills/{id}/split` |
| Summary.dc.html | 4 · Here's the damage | `/bills/{id}/send` |
| Bill.dc.html | 5 · Open bill | `/bills/{id}` |
| Kitchen.dc.html | K1 · Kitchen (house landing) | `/` |
| Fridge.dc.html | K2 · Fridge | `/kitchen/fridge` |
| Pantry.dc.html | K3 · Pantry (opens with the ask sheet showing) | `/kitchen/pantry` |
| Requests.dc.html | K4 · Share requests | `/kitchen/requests` |
| Notify.dc.html | N1 · Turn on notifications (iPhone / Android tabs) | sheet, PRD §20.2 |
| NotifySettings.dc.html | N2 · Settings › Notifications | `/settings/notifications` |

## How to read the files

They are written in a small template format, not plain HTML, so they will not render
by opening them in a browser. Read them as annotated markup:

- Everything inside `<x-dc>` is the screen. Inline `style="…"` attributes hold the exact
  sizes, spacing, borders, radii, shadows and font settings to reproduce.
- `{{c.ink}}`, `{{c.paper}}` and similar are theme colors. Their light and dark values are
  defined in the `c = dark ? {...} : {...}` object at the bottom of each file and map
  one-to-one to the CSS tokens in PRD §14 (`bg`, `paper`, `ink`, `muted`, `line`→ink,
  `faint`, `shadow`, `accent`, `accent2`, `onAccent2`, `warn`, `good`).
- `{{name}}` holes are filled from `renderVals()` in the `<script>` block at the bottom.
  That function also holds the sample data (items, prices, members) and any interaction
  logic (for example the checkbox toggling and live totals in `Split.dc.html`).
- `<sc-for list="{{items}}" as="item">` is a loop; `<sc-if value="{{flag}}">` is a
  conditional. `onClick="{{fn}}"` binds a handler from `renderVals()`.
- `<helmet>` holds the Google Fonts link (Space Mono + Work Sans). Self-host them in the
  real app.
- `<a href="Other.dc.html">` links show the navigation between screens.

## Things the mockups do not decide

- **Amounts are sample data.** The mockups round the leftover cent to the payer; the real
  rule is largest-remainder per item (PRD §6), so real shares differ by a few cents. For
  example, the ShopRite bill is $23.51 / $33.91 / $18.87 / $18.89 in
  `packages/fixtures/money_cases.json`, not the mockup's $23.48 / $33.91 / $18.88 / $18.91.
  Always trust the fixtures.
- **Kitchen sample states are illustrative.** Which items show as USE SOON, their days left and their sharing states are picked to show every state, not computed from the shelf-life rules in PRD §19.5. Fridge and Pantry share one template (the data block between `DATA-START` and `DATA-END` differs); build them as one component.
- **Food icons:** the 15 icons drawn in `Fridge.dc.html` (`ICONS` object) are final; draw the rest of the set in PRD §19.8 in the same style.
- **The "G" sign-in button is a placeholder.** Use Google's official sign-in button.
- **`kitchentab.app` is a placeholder domain.**
- **Contrast fixes after approval (PRD §14):** the violet member color is #6A4CFF (white initial),
  not the mockups' #7B61FF, and empty controls (unchecked split boxes, empty OTP boxes) use a
  `--muted` border instead of `--faint`. The renders in `renders/` predate this change.
- **Screens with no mockup** (pay sheet, settings, house settings, dialogs, error and
  offline states) are specified in words in PRD §14A. Build them in the same style.
