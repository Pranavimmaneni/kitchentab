// PERF-01 (TEST_PLAN 3.11; FR-60, FR-62): 200 people editing 50 open bills at once, four per bill.
// Driven by scripts/load_test.py, which builds the houses and writes /out/perf01-world.json.
// Each person ticks and unticks themselves (set-state PUT), reads the bill and their tabs, and the
// payer sometimes edits a price with If-Match. Pass: p95 < 300 ms and no errors.
import http from "k6/http";
import { check, sleep } from "k6";
import exec from "k6/execution";

const world = JSON.parse(open("/out/perf01-world.json"));
const BASES = __ENV.BASE_URLS.split(","); // several API instances, like serverless (ADR-0029)
const PEOPLE = world.houses.length * 4;

export const options = {
  scenarios: {
    editors: {
      executor: "constant-vus",
      vus: PEOPLE,
      duration: __ENV.DURATION || "120s",
    },
  },
  thresholds: {
    "http_req_duration{api:yes}": ["p(95)<300"],
    http_req_failed: ["rate==0"],
  },
  summaryTrendStats: ["avg", "min", "med", "max", "p(90)", "p(95)", "p(99)"],
};

function who() {
  const n = exec.vu.idInTest - 1;
  const house = world.houses[Math.floor(n / 4)];
  const seat = n % 4;
  return { house, seat, base: BASES[n % BASES.length] };
}

function params(h, seat, extra) {
  const csrf = h.csrf[seat];
  return {
    headers: Object.assign(
      {
        "Content-Type": "application/json",
        Cookie: `${__ENV.SESSION_COOKIE}=${h.people[seat]}; ${__ENV.CSRF_COOKIE}=${csrf}`,
        [__ENV.CSRF_HEADER]: csrf,
      },
      extra || {},
    ),
    tags: { api: "yes" },
    // 409/412/428 are answers (a stale If-Match after someone else's edit), not failures
    responseCallback: http.expectedStatuses(200, 201, 409, 412),
  };
}

export default function () {
  const { house: h, seat, base: BASE } = who();
  const member = h.members[seat];
  const item = h.items[Math.floor(Math.random() * h.items.length)];
  const roll = Math.random();
  if (roll < 0.55) {
    const body = JSON.stringify({ changes: [{ item_id: item, participant: `m:${member}`, on: Math.random() < 0.5 }] });
    const r = http.put(`${BASE}/api/v1/bills/${h.bill}/allocations`, body, params(h, seat));
    check(r, { "tick 200": (x) => x.status === 200 });
  } else if (roll < 0.85) {
    const r = http.get(`${BASE}/api/v1/bills/${h.bill}`, params(h, seat));
    check(r, { "bill 200": (x) => x.status === 200 });
  } else if (roll < 0.95 || seat !== 0) {
    const r = http.get(`${BASE}/api/v1/houses/${h.house}/tabs`, params(h, seat));
    check(r, { "tabs 200": (x) => x.status === 200 });
  } else {
    const view = http.get(`${BASE}/api/v1/bills/${h.bill}`, params(h, seat));
    if (view.status === 200) {
      const it = view.json().items.find((i) => i.id === item);
      if (it) {
        const cents = Math.max(1, it.line_total_cents + (Math.random() < 0.5 ? -1 : 1));
        const r = http.patch(
          `${BASE}/api/v1/bills/${h.bill}/items/${item}`,
          JSON.stringify({ line_total_cents: cents }),
          params(h, seat, { "If-Match": String(it.version) }),
        );
        check(r, { "price edit answered": (x) => x.status === 200 || x.status === 412 });
      }
    }
  }
  sleep(0.5 + Math.random());
}
