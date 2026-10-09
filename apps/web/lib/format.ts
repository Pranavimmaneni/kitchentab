/**
 * Display formatting only. Money arrives as integer cents from the server; nothing here
 * computes shares or totals (CLAUDE.md rule 5).
 */
const formatters = new Map<string, Intl.NumberFormat>();

export function formatMoney(cents: number, currency = "USD"): string {
  if (!Number.isInteger(cents)) throw new TypeError(`Money must be integer cents, got ${cents}`);
  let formatter = formatters.get(currency);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-US", { style: "currency", currency });
    formatters.set(currency, formatter);
  }
  return formatter.format(cents / 100);
}
