import type { Page } from "@playwright/test";
import { call, hydrated } from "./fixtures/app";
import { expect, test } from "./fixtures/roommates";
import { apartment, box, isCatchUp, isStream, openBill } from "./fixtures/sync";

/**
 * Milestone 6 multi-roommate browser tests (TEST_PLAN §3.7 level 2): several browser contexts,
 * each signed in as a different roommate against the real API, converging within 2 s through
 * the house stream (PRD §21).
 */

// Each test runs 2–4 browsers at once; run this file's tests one after another so the
// convergence windows measure sync, not a saturated machine.
test.describe.configure({ mode: "default" });

const BANANAS = "Bananas 2.1 lb";
const CHICKEN = "Chicken thighs 3 lb";
const EGGS = "Eggs 18 ct";

async function itemsView(page: Page): Promise<void> {
  await page.getByRole("tab", { name: "Items & splits" }).click();
  await expect(page.getByRole("grid", { name: "Who's eating what" })).toBeVisible();
}

test.describe("screen 5 live sync", () => {
  test("SYNC-01 two roommates tick themselves on the same item at once and all three devices show both ticks within 2 s [FR-32] [FR-33]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(3);
    const apt = await apartment(house, ["arjun", "meera"]);
    const [payer, arjun, meera] = [apt.as.pranav!, apt.as.arjun!, apt.as.meera!];
    await Promise.all([payer, arjun, meera].map((p) => openBill(p, apt.billId)));
    await Promise.all([payer, arjun, meera].map(itemsView));

    // Neither is on the bananas yet; both hop on at the same moment.
    await expect(box(arjun, true, "Arjun", BANANAS)).toHaveAttribute("aria-pressed", "false");
    await Promise.all([
      box(arjun, true, "Arjun", BANANAS).click(),
      box(meera, true, "Meera", BANANAS).click(),
    ]);

    await house.expectEverywhere(
      async (page, mate) => {
        const self = mate.index === 1 ? "Arjun" : mate.index === 2 ? "Meera" : null;
        for (const name of ["Arjun", "Meera"]) {
          await expect(box(page, self === name, name, BANANAS)).toHaveAttribute(
            "aria-pressed",
            "true",
            { timeout: 50 },
          );
        }
      },
      { within: 2000 },
    );
    const bill = await call<{ items: { name: string; allocations: { participant: string }[] }[] }>(
      payer,
      "GET",
      `/bills/${apt.billId}`,
    );
    const bananas = bill.items.find((i) => i.name === BANANAS)!;
    expect(bananas.allocations.map((a) => a.participant).sort()).toEqual(
      [apt.member.pranav, apt.member.kabir, apt.member.arjun, apt.member.meera]
        .map((id) => `m:${id}`)
        .sort(),
    );
  });

  test("SYNC-03 the last two people untick an item at once: one wins, the other sees why and its tick comes back [FR-34]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["arjun"]);
    const [payer, arjun] = [apt.as.pranav!, apt.as.arjun!];
    await Promise.all([openBill(payer, apt.billId), openBill(arjun, apt.billId)]);
    await Promise.all([itemsView(payer), itemsView(arjun)]);

    // Chicken is Pranav's and Arjun's only.
    await Promise.all([
      box(payer, true, "Pranav", CHICKEN).click(),
      box(arjun, true, "Arjun", CHICKEN).click(),
    ]);

    const lastPerson = "You're the last one on it. Ask the payer to remove the item.";
    await expect
      .poll(
        async () =>
          (await payer.getByText(lastPerson).count()) + (await arjun.getByText(lastPerson).count()),
        {
          timeout: 5000,
        },
      )
      .toBe(1);
    await house.expectEverywhere(async (page, mate) => {
      const pressed = await page
        .getByRole("button", { name: new RegExp(` uses ${CHICKEN}$`) })
        .evaluateAll(
          (els) => els.filter((el) => el.getAttribute("aria-pressed") === "true").length,
        );
      expect(pressed, `roommate ${mate.index}`).toBe(1);
    });
  });

  test("SYNC-32 a roommate's own tick never flickers when its echo comes back [SYNC-20] [FR-32]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["meera"]);
    const [payer, meera] = [apt.as.pranav!, apt.as.meera!];
    await Promise.all([openBill(payer, apt.billId), openBill(meera, apt.billId)]);
    await Promise.all([itemsView(payer), itemsView(meera)]);

    const target = box(meera, true, "Meera", EGGS);
    await expect(target).toHaveAttribute("aria-pressed", "true");
    // Record every aria-pressed change on Meera's box for 2.5 s after she unticks it.
    await target.evaluate((el) => {
      const seen: string[] = [];
      (window as unknown as { __pressed: string[] }).__pressed = seen;
      new MutationObserver(() => seen.push(String(el.getAttribute("aria-pressed")))).observe(el, {
        attributes: true,
        attributeFilter: ["aria-pressed"],
      });
    });
    await target.click();
    await expect(box(payer, false, "Meera", EGGS)).toHaveAttribute("aria-pressed", "false", {
      timeout: 2000,
    });
    await meera.waitForTimeout(2500);
    const history = await meera.evaluate(
      () => (window as unknown as { __pressed: string[] }).__pressed,
    );
    expect(history).toEqual(["false"]);
  });

  test("PAY-01 a member records a payment, the payer sees it pending within 2 s and confirms, the member sees it paid [FR-36] [FR-37] BAL-02", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["kabir"]);
    const [payer, kabir] = [apt.as.pranav!, apt.as.kabir!];
    await Promise.all([openBill(payer, apt.billId), openBill(kabir, apt.billId)]);

    await kabir.getByRole("button", { name: "Pay $18.89" }).click();
    const sheet = kabir.getByRole("dialog", { name: "Pay Pranav $18.89" });
    await expect(sheet).toBeVisible();
    await expect(
      sheet.getByText(
        "Pranav hasn't added payment info. Pay however you usually do, then mark it.",
      ),
    ).toBeVisible();
    await expect(sheet.getByLabel("AMOUNT")).toHaveValue("18.89");
    await sheet.getByRole("button", { name: "I've paid — mark it" }).click();
    await expect(kabir.getByText("Marked. Pranav will confirm.")).toBeVisible();
    await expect(kabir.getByRole("button", { name: "Paid · waiting on Pranav" })).toBeDisabled();

    await expect(payer.getByText("PAID $18.89 · PENDING")).toBeVisible({ timeout: 2000 });
    await payer.getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(payer.getByText("Confirmed Kabir's $18.89.", { exact: true })).toBeVisible();
    await expect(kabir.getByText("PAID $18.89", { exact: true })).toBeVisible({ timeout: 2000 });
    await expect(kabir.getByText("Pranav confirmed your $18.89.")).toBeVisible({ timeout: 2000 });
    const money = await call<{ owed: Record<string, number> }>(
      payer,
      "GET",
      `/bills/${apt.billId}/money`,
    );
    expect(money.owed[`m:${apt.member.kabir}`]).toBe(0);
  });

  test("SYNC-06 the payer stamps it settled while a member is paying: the member's write is refused with the settled copy and both screens show SETTLED [FR-39] [FR-36] BILL-09", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["arjun"]);
    const [payer, arjun] = [apt.as.pranav!, apt.as.arjun!];
    await Promise.all([openBill(payer, apt.billId), openBill(arjun, apt.billId)]);

    await arjun.getByRole("button", { name: "Pay $33.91" }).click();
    const sheet = arjun.getByRole("dialog", { name: "Pay Pranav $33.91" });
    await expect(sheet).toBeVisible();

    await payer.getByRole("button", { name: "Stamp it settled" }).click();
    const dialog = payer.getByRole("dialog", { name: "Stamp it settled?" });
    await expect(dialog.getByText("Arjun still owes $33.91")).toBeVisible();
    await expect(dialog.getByText("This moves to house balances.")).toBeVisible();
    await dialog.getByRole("button", { name: "Stamp it" }).click();
    await expect(payer.getByText(/^SETTLED \d\d\/\d\d$/)).toBeVisible();

    await sheet.getByRole("button", { name: "I've paid — mark it" }).click();
    await expect(arjun.getByText("This tab was just settled.")).toBeVisible();
    await house.expectEverywhere(async (page) => {
      await expect(page.getByText(/^SETTLED \d\d\/\d\d$/)).toBeVisible({ timeout: 50 });
    });
    const money = await call<{ payments: unknown[] }>(payer, "GET", `/bills/${apt.billId}/money`);
    expect(money.payments).toHaveLength(0);
    // Read-only now; the payer can reopen within 30 days (FR-40).
    await expect(arjun.getByRole("button", { name: /^Pay / })).toHaveCount(0);
    await payer.getByRole("button", { name: "Reopen" }).click();
    await house.expectEverywhere(async (page) => {
      await expect(page.getByText("STILL OPEN · DAY 1")).toBeVisible({ timeout: 50 });
    });
  });

  test("SYNC-05 a stale If-Match on an item edit shows who changed it with theirs vs yours, and Keep mine re-applies [FR-33]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["meera"]);
    const [payer, meera] = [apt.as.pranav!, apt.as.meera!];
    await Promise.all([openBill(payer, apt.billId), openBill(meera, apt.billId)]);
    await Promise.all([itemsView(payer), itemsView(meera)]);

    await payer.getByRole("button", { name: `Edit ${EGGS}` }).click();
    const editor = payer.getByRole("dialog", { name: "Edit item" });
    await expect(editor.getByLabel("LINE TOTAL")).toHaveValue("5.49");
    // Meanwhile the payer's other device changes the price (same row, newer version).
    const bill = await call<{ items: { id: string; version: number }[] }>(
      payer,
      "GET",
      `/bills/${apt.billId}`,
    );
    const version = bill.items.find((i) => i.id === apt.itemIds.eggs)!.version;
    await call(
      payer,
      "PATCH",
      `/bills/${apt.billId}/items/${apt.itemIds.eggs}`,
      { line_total_cents: 600 },
      { ifMatch: version },
    );
    // The stream brings the change (and who made it) to every device, the open editor keeps
    // the payer's stale version.
    await house.expectEverywhere(async (page) => {
      await expect(
        page.getByRole("row").filter({ hasText: EGGS }).getByText("$6.00", { exact: true }),
      ).toBeAttached({
        timeout: 50,
      });
    });
    await editor.getByLabel("LINE TOTAL").fill("7.00");
    await editor.getByRole("button", { name: "Save" }).click();

    const conflict = payer.getByRole("dialog", { name: "You just changed this" });
    await expect(conflict).toBeVisible();
    await expect(conflict.getByText("THEIRS", { exact: true })).toBeVisible();
    await expect(conflict.getByText("$6.00", { exact: true })).toBeVisible();
    await expect(conflict.getByText("YOURS", { exact: true })).toBeVisible();
    await expect(conflict.getByText("$7.00", { exact: true })).toBeVisible();
    await conflict.getByRole("button", { name: "Keep mine" }).click();
    await expect(conflict).toBeHidden();

    await house.expectEverywhere(async (page) => {
      const row = page.getByRole("row").filter({ hasText: EGGS });
      await expect(row.getByText("$7.00", { exact: true })).toBeVisible({ timeout: 50 });
    });
  });

  test("OFFLINE-01 offline: banner, every write on screen 5 disabled with 'Needs a connection', nothing queued; back online it catches up [OFFLINE-02] [OFFLINE-04] [FR-36]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["arjun"]);
    const [payer, arjun] = [apt.as.pranav!, apt.as.arjun!];
    await Promise.all([openBill(payer, apt.billId), openBill(arjun, apt.billId)]);

    const writes: string[] = [];
    arjun.on("request", (r) => {
      if (r.method() !== "GET") writes.push(`${r.method()} ${r.url()}`);
    });
    await arjun.context().setOffline(true);
    await expect(arjun.getByText("YOU'RE OFFLINE · SHOWING SAVED TABS")).toBeVisible();
    const pay = arjun.getByRole("button", { name: "Pay $33.91" });
    await expect(pay).toBeDisabled();
    await expect(arjun.getByText("Needs a connection")).toBeVisible();
    await itemsView(arjun);
    await expect(box(arjun, true, "Arjun", EGGS)).toBeDisabled();
    await pay.click({ force: true }).catch(() => undefined);
    await box(arjun, true, "Arjun", EGGS)
      .click({ force: true })
      .catch(() => undefined);

    // A roommate's change while Arjun is offline.
    await call(payer, "PUT", `/bills/${apt.billId}/allocations`, {
      changes: [{ item_id: apt.itemIds.yogurt, participant: `m:${apt.member.arjun}`, on: true }],
    });
    await arjun.waitForTimeout(500);
    expect(writes).toEqual([]);

    await arjun.context().setOffline(false);
    await expect(arjun.getByText("YOU'RE OFFLINE · SHOWING SAVED TABS")).toBeHidden();
    await expect(box(arjun, true, "Arjun", "Greek yogurt 32 oz")).toHaveAttribute(
      "aria-pressed",
      "true",
      {
        timeout: 5000,
      },
    );
    await expect(box(arjun, true, "Arjun", EGGS)).toBeEnabled();
    expect(writes).toEqual([]);
  });

  test("SPLIT-08 a roommate's change shows the lime 'Your share changed' banner live; dismissing clears it for good [FR-35] SYNC-01", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(3);
    const apt = await apartment(house, ["arjun", "meera"]);
    const [arjun, meera] = [apt.as.arjun!, apt.as.meera!];
    await openBill(arjun, apt.billId);
    await expect(arjun.getByText(/Your share changed/)).toHaveCount(0);

    // Meera leaves the milk: Arjun's share goes up.
    await call(meera, "PUT", `/bills/${apt.billId}/allocations`, {
      changes: [{ item_id: apt.itemIds.milk, participant: `m:${apt.member.meera}`, on: false }],
    });
    const banner = arjun.getByText(/^Your share changed \+\$\d+\.\d\d since you last looked\.$/);
    await expect(banner).toBeVisible({ timeout: 2000 });
    await arjun.getByRole("button", { name: "Dismiss" }).click();
    await expect(banner).toBeHidden();
    await arjun.reload();
    await hydrated(arjun);
    await expect(arjun.getByRole("heading", { name: "WHO OWES WHAT" })).toBeVisible();
    await expect(arjun.getByText(/Your share changed/)).toHaveCount(0);
  });

  test("BILL-08 the payer voids with a reason from the ⋯ menu; every device shows VOIDED and the reason [FR-42] PAY-07", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["meera"]);
    const [payer, meera] = [apt.as.pranav!, apt.as.meera!];
    await Promise.all([openBill(payer, apt.billId), openBill(meera, apt.billId)]);

    // PAY-07: one nudge per member per 48 h.
    await payer.getByRole("button", { name: "Nudge Meera" }).click();
    await expect(payer.getByText("Nudged Meera · next nudge in 48 h")).toBeVisible();
    await expect(payer.getByRole("button", { name: "Nudge Meera" })).toBeDisabled();
    await expect(meera.getByText("Pranav nudged you.")).toBeVisible({ timeout: 2000 });

    await payer.getByRole("button", { name: "More actions" }).click();
    await payer
      .getByRole("dialog", { name: "Tab actions" })
      .getByRole("button", { name: "Void bill" })
      .click();
    const dialog = payer.getByRole("dialog", { name: "Void this tab?" });
    await dialog.getByRole("button", { name: "Void this tab" }).click();
    await expect(dialog.getByText("Say why in 3 to 200 characters.")).toBeVisible();
    await dialog.getByLabel("WHY? THIS GOES IN THE LOG").fill("Wrong receipt");
    await dialog.getByRole("button", { name: "Void this tab" }).click();
    await house.expectEverywhere(async (page) => {
      await expect(page.getByText("VOIDED", { exact: true })).toBeVisible({ timeout: 50 });
      await expect(page.getByText("Voided: Wrong receipt")).toBeVisible({ timeout: 50 });
    });
    await expect(meera.getByRole("button", { name: /^Pay / })).toHaveCount(0);
  });

  test("SYNC-12 an admin removes a member who has the bill open: their screen leaves the house with a notice [FR-06] SYNC-33", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["kabir"]);
    const [payer, kabir] = [apt.as.pranav!, apt.as.kabir!];
    await openBill(kabir, apt.billId);
    await call(payer, "DELETE", `/houses/${apt.houseId}/members/${apt.member.kabir}`);
    await expect(kabir.getByText("You're not in Apt 4B any more.")).toBeVisible({ timeout: 5000 });
    await expect(kabir).not.toHaveURL(new RegExp(`/bills/${apt.billId}$`));
    // A write with the old page state is refused server-side as well.
    const res = await kabir.request.get(`/api/v1/bills/${apt.billId}`);
    expect(res.status()).toBe(403);
  });
});

test.describe("more races on screen 5", () => {
  test("SYNC-02 a member unticks themself while the payer unticks the same member: off on every device, no flicker back [FR-32] [FR-33]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["arjun"]);
    const [payer, arjun] = [apt.as.pranav!, apt.as.arjun!];
    await Promise.all([openBill(payer, apt.billId), openBill(arjun, apt.billId)]);
    await Promise.all([itemsView(payer), itemsView(arjun)]);
    await expect(box(arjun, true, "Arjun", EGGS)).toHaveAttribute("aria-pressed", "true");

    await Promise.all([
      box(arjun, true, "Arjun", EGGS).click(),
      box(payer, false, "Arjun", EGGS).click(),
    ]);
    await house.expectEverywhere(async (page, mate) => {
      await expect(box(page, mate.index === 1, "Arjun", EGGS)).toHaveAttribute(
        "aria-pressed",
        "false",
        { timeout: 50 },
      );
    });
    // And it stays off once both echoes are in.
    await arjun.waitForTimeout(1000);
    await house.expectEverywhere(async (page, mate) => {
      await expect(box(page, mate.index === 1, "Arjun", EGGS)).toHaveAttribute(
        "aria-pressed",
        "false",
        { timeout: 50 },
      );
    });
    const bill = await call<{ items: { name: string; allocations: { participant: string }[] }[] }>(
      payer,
      "GET",
      `/bills/${apt.billId}`,
    );
    expect(
      bill.items.find((i) => i.name === EGGS)!.allocations.map((a) => a.participant),
    ).not.toContain(`m:${apt.member.arjun}`);
  });

  test("SYNC-04 the payer changes a price while a member ticks that item: both land, and every screen shows the new price and the tick [FR-33] [FR-32]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["arjun"]);
    const [payer, arjun] = [apt.as.pranav!, apt.as.arjun!];
    await Promise.all([openBill(payer, apt.billId), openBill(arjun, apt.billId)]);
    await Promise.all([itemsView(payer), itemsView(arjun)]);
    const bill = await call<{ items: { id: string; name: string; version: number }[] }>(
      payer,
      "GET",
      `/bills/${apt.billId}`,
    );
    const bananas = bill.items.find((i) => i.name === BANANAS)!;

    await Promise.all([
      box(arjun, true, "Arjun", BANANAS).click(),
      call(
        payer,
        "PATCH",
        `/bills/${apt.billId}/items/${bananas.id}`,
        { line_total_cents: 200 },
        { ifMatch: bananas.version },
      ),
    ]);
    await house.expectEverywhere(async (page, mate) => {
      await expect(box(page, mate.index === 1, "Arjun", BANANAS)).toHaveAttribute(
        "aria-pressed",
        "true",
        { timeout: 50 },
      );
      await expect(page.getByRole("row").filter({ hasText: BANANAS })).toContainText("$2.00", {
        timeout: 50,
      });
    });
  });

  test("SYNC-09 SYNC-10 a payment marked from two tabs at once is kept twice and the second says 'You already recorded'; a double tap keeps one [FR-36]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["kabir"]);
    const [payer, kabir] = [apt.as.pranav!, apt.as.kabir!];
    const second = await kabir.context().newPage();
    await openBill(payer, apt.billId);
    await openBill(kabir, apt.billId);
    await second.goto(`/bills/${apt.billId}`);
    await hydrated(second);

    // Both tabs have the pay sheet open before either marks it.
    const sheetOf = async (page: Page) => {
      await page.getByRole("button", { name: "Pay $18.89" }).click();
      const sheet = page.getByRole("dialog", { name: "Pay Pranav $18.89" });
      await expect(sheet).toBeVisible();
      return sheet;
    };
    const [first, other] = [await sheetOf(kabir), await sheetOf(second)];
    // SYNC-09: a double tap on one device sends one payment.
    await first.getByRole("button", { name: "I've paid — mark it" }).dblclick();
    await expect(kabir.getByText("Marked. Pranav will confirm.")).toBeVisible();
    // SYNC-10: the other device, a new key: kept, with the heads-up.
    await other.getByRole("button", { name: "I've paid — mark it" }).click();
    await expect(second.getByText("You already recorded $18.89 just now")).toBeVisible();
    const money = await call<{ payments: { amount_cents: number; status: string }[] }>(
      payer,
      "GET",
      `/bills/${apt.billId}/money`,
    );
    expect(money.payments.filter((p) => p.status === "pending_confirmation")).toHaveLength(2);
    // The payer sees the pending payments live and can reject one.
    await expect(payer.getByRole("button", { name: "Reject", exact: true }).first()).toBeVisible({
      timeout: 2000,
    });
  });

  test("SYNC-23 the payer deletes an item a member is ticking: the tick is refused as gone and the row leaves every screen [FR-33] [FR-32]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["meera"]);
    const [payer, meera] = [apt.as.pranav!, apt.as.meera!];
    await Promise.all([openBill(payer, apt.billId), openBill(meera, apt.billId)]);
    await Promise.all([itemsView(payer), itemsView(meera)]);

    // Meera's tick leaves her browser just as the payer's delete commits.
    await meera.route(/\/api\/v1\/bills\/[^/]+\/allocations$/, async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      await call(payer, "DELETE", `/bills/${apt.billId}/items/${apt.itemIds.bars}`);
      return route.continue();
    });
    await box(meera, true, "Meera", "Protein bars 12 ct").click();
    await expect(meera.getByText("That item was removed.")).toBeVisible();
    await house.expectEverywhere(async (page) => {
      await expect(page.getByRole("row").filter({ hasText: "Protein bars 12 ct" })).toHaveCount(0, {
        timeout: 50,
      });
    });
  });
});

test.describe("house screens live", () => {
  test("BAL-02 E2E-03 a confirmed payment moves the balance card and Balances on every device within 2 s [FR-37] [FR-50] [FR-51] @multi", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(3);
    const apt = await apartment(house, ["arjun", "meera"]);
    const [payer, arjun, meera] = [apt.as.pranav!, apt.as.arjun!, apt.as.meera!];
    // Pranav on Tabs, Arjun and Meera on Balances, all with their house stream up.
    await Promise.all([
      (async () => {
        const stream = payer.waitForRequest(isStream, { timeout: 30_000 });
        await payer.goto("/tabs");
        await hydrated(payer);
        await stream;
      })(),
      ...[arjun, meera].map(async (page) => {
        const stream = page.waitForRequest(isStream, { timeout: 30_000 });
        await page.goto("/balances");
        await hydrated(page);
        await stream;
      }),
    ]);
    const card = payer.getByRole("region", { name: "BALANCE" });
    await expect(card.getByText("+$71.67")).toBeVisible();
    await expect(arjun.getByRole("button", { name: /^You owe Pranav\s*\$33\.91/ })).toBeVisible();
    await expect(meera.getByRole("button", { name: /^You owe Pranav\s*\$18\.87/ })).toBeVisible();

    // Arjun marks $33.91 paid; a pending payment moves nothing yet.
    const payment = await call<{ id: string }>(arjun, "POST", `/bills/${apt.billId}/payments`, {
      amount_cents: 3391,
      method: "venmo",
    });
    await call(payer, "POST", `/payments/${payment.id}/confirm`);
    await house.expectEverywhere(async (page, mate) => {
      if (mate.index === 0) {
        await expect(
          page.getByRole("region", { name: "BALANCE" }).getByText("+$37.76"),
        ).toBeVisible({ timeout: 50 });
        await expect(page.getByText(/ARJUN\s*→/)).toHaveCount(0, { timeout: 50 });
      } else if (mate.index === 1) {
        await expect(page.getByRole("button", { name: /^You owe Pranav/ })).toHaveCount(0, {
          timeout: 50,
        });
        await expect(page.getByText("ALL SQUARE").first()).toBeVisible({ timeout: 50 });
      } else {
        // Meera's own balance is untouched, and still right.
        await expect(page.getByRole("button", { name: /^You owe Pranav\s*\$18\.87/ })).toBeVisible({
          timeout: 50,
        });
      }
    });
  });
});

test.describe("one stream per browser", () => {
  test("SYNC-31 two tabs of one roommate elect one stream leader; closing the leader hands over within 3 s [SYNC-20]", async ({
    roommates,
  }) => {
    test.setTimeout(120_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["meera"]);
    const payer = apt.as.pranav!;
    const tabA = apt.as.meera!;
    const tabB = await tabA.context().newPage();
    const streams = new Map<Page, number>([
      [tabA, 0],
      [tabB, 0],
    ]);
    for (const page of [tabA, tabB])
      page.on("request", (r) => isStream(r) && streams.set(page, streams.get(page)! + 1));

    await openBill(tabA, apt.billId);
    await tabB.goto(`/bills/${apt.billId}`);
    await hydrated(tabB);
    await expect(tabB.getByRole("heading", { name: "WHO OWES WHAT" })).toBeVisible();
    await tabB.waitForTimeout(1500);
    expect(streams.get(tabA)).toBe(1);
    expect(streams.get(tabB)).toBe(0);

    // The follower tab is still live through the leader's relay.
    await itemsView(tabB);
    await call(payer, "PUT", `/bills/${apt.billId}/allocations`, {
      changes: [{ item_id: apt.itemIds.bars, participant: `m:${apt.member.meera}`, on: true }],
    });
    await expect(box(tabB, true, "Meera", "Protein bars 12 ct")).toHaveAttribute(
      "aria-pressed",
      "true",
      {
        timeout: 2000,
      },
    );

    // Close the leader: the other tab takes over the stream within 3 s and keeps converging.
    const handover = tabB.waitForRequest(isStream, { timeout: 3000 });
    await tabA.close();
    await handover;
    await call(payer, "PUT", `/bills/${apt.billId}/allocations`, {
      changes: [{ item_id: apt.itemIds.bars, participant: `m:${apt.member.meera}`, on: false }],
    });
    await expect(box(tabB, true, "Meera", "Protein bars 12 ct")).toHaveAttribute(
      "aria-pressed",
      "false",
      {
        timeout: 2000,
      },
    );
  });

  test("SYNC-34 when the stream keeps failing the page polls ?after= every 15 s, and stops once the stream is back [SYNC-21]", async ({
    roommates,
    browserName,
  }) => {
    test.skip(browserName !== "chromium", "timing-heavy fallback check runs once, on Chromium");
    test.setTimeout(150_000);
    const house = await roommates(2);
    const apt = await apartment(house, ["meera"]);
    const [payer, meera] = [apt.as.pranav!, apt.as.meera!];

    // Every stream connect fails (a proxy that refuses event streams); plain GETs still work.
    let blocked = true;
    await meera.route(/\/api\/v1\/houses\/[^/]+\/events(\?last_event_id=\d+)?$/, (route) =>
      blocked ? route.fulfill({ status: 503, body: "" }) : route.continue(),
    );
    const polls: number[] = [];
    const streams: number[] = [];
    meera.on("request", (r) => {
      if (isCatchUp(r)) polls.push(Date.now());
      if (isStream(r)) streams.push(Date.now());
    });
    await openBill(meera, apt.billId);
    await itemsView(meera);

    // Three failed connects (about 1 + 2 s of backoff), then the first poll.
    await expect.poll(() => polls.length, { timeout: 15_000 }).toBeGreaterThan(0);
    expect(streams.length).toBeGreaterThanOrEqual(3);
    await call(payer, "PUT", `/bills/${apt.billId}/allocations`, {
      changes: [{ item_id: apt.itemIds.coffee, participant: `m:${apt.member.meera}`, on: true }],
    });
    // The next poll (≤ 15 s) brings the change.
    await expect(box(meera, true, "Meera", "Coffee beans 12 oz")).toHaveAttribute(
      "aria-pressed",
      "true",
      {
        timeout: 20_000,
      },
    );
    const intervals = polls.slice(1).map((t, i) => t - polls[i]!);
    for (const gap of intervals) expect(gap).toBeGreaterThan(14_000);

    // The stream comes back on the next reconnect (backoff ≤ 30 s); polling stops.
    blocked = false;
    const recovered = streams.length;
    await expect.poll(() => streams.length, { timeout: 40_000 }).toBeGreaterThan(recovered);
    await meera.waitForTimeout(1000);
    const before = polls.length;
    await meera.waitForTimeout(16_000);
    expect(polls.length).toBe(before);
    // And it is live again.
    await call(payer, "PUT", `/bills/${apt.billId}/allocations`, {
      changes: [{ item_id: apt.itemIds.coffee, participant: `m:${apt.member.meera}`, on: false }],
    });
    await expect(box(meera, true, "Meera", "Coffee beans 12 oz")).toHaveAttribute(
      "aria-pressed",
      "false",
      {
        timeout: 2000,
      },
    );
  });
});
