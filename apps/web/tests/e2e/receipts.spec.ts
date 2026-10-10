import { readFileSync } from "node:fs";
import { expect, test, type Page, type Request } from "@playwright/test";
import { call, createHouse, hydrated, uniqueEmail } from "./fixtures/app";
import { apiSignIn } from "./fixtures/household";
import {
  billIdFromUrl,
  billOf,
  blankPdf,
  blankPng,
  lineScore,
  receiptFile,
  truth,
  waitForReading,
  withExifGps,
} from "./fixtures/receipts";

/**
 * Receipt capture and reading in the browser (PRD §9, §14A 1–2; BUILD_ALL C1): the capture sheet,
 * on-device checks, EXIF stripping, client upload to the compose MinIO, text PDFs parsed by the
 * API, photos OCR'd with tesseract.js from /ocr/, the failure state and the scanned-items offer.
 */

async function signedInHouse(page: Page): Promise<string> {
  await apiSignIn(page, uniqueEmail("pranav"));
  const house = await createHouse(page);
  return house.id;
}

async function openCapture(page: Page, houseId: string): Promise<void> {
  await page.goto(`/bills/new?house=${houseId}`);
  await hydrated(page);
  await expect(page.getByRole("dialog", { name: "Snap a receipt" })).toBeVisible();
  await page.locator("[data-kt-capture-ready]").waitFor({ state: "attached" });
}

const library = (page: Page) => page.locator('input[data-kt-input="library"]');

test.describe("capture and refusals", () => {
  test("EXTRACT-01 type, size and page-count refusals happen on the device, before anything is created or uploaded [FR-10]", async ({
    page,
  }) => {
    const houseId = await signedInHouse(page);
    await openCapture(page, houseId);
    const writes: string[] = [];
    page.on("request", (r: Request) => {
      if (r.method() !== "GET") writes.push(`${r.method()} ${new URL(r.url()).pathname}`);
    });
    const sheet = page.getByRole("dialog", { name: "Snap a receipt" });

    await library(page).setInputFiles({
      name: "receipt.jpg",
      mimeType: "image/jpeg",
      buffer: Buffer.from("not really a photo"),
    });
    await expect(sheet.getByRole("alert")).toHaveText(
      "That file type isn't supported. Use a photo (JPEG, PNG, HEIC, WEBP) or a PDF.",
    );

    const big = Buffer.alloc(20 * 1024 * 1024 + 1);
    big.set([0xff, 0xd8, 0xff, 0xe0]);
    await library(page).setInputFiles({ name: "huge.jpg", mimeType: "image/jpeg", buffer: big });
    await expect(sheet.getByRole("alert")).toHaveText(
      "That file is over 20 MB. Try a smaller photo or PDF.",
    );

    await page.locator('input[data-kt-input="pdf"]').setInputFiles({
      name: "long.pdf",
      mimeType: "application/pdf",
      buffer: blankPdf(11),
    });
    await expect(sheet.getByRole("alert")).toHaveText("That PDF has more than 10 pages.");

    await library(page).setInputFiles([
      { name: "a.pdf", mimeType: "application/pdf", buffer: blankPdf(1) },
      { name: "b.png", mimeType: "image/png", buffer: blankPng(10, 10) },
    ]);
    await expect(sheet.getByRole("alert")).toHaveText("Pick photos or one PDF, not both.");

    await expect(page).toHaveURL(/\/bills\/new/);
    expect(writes.filter((w) => w.includes("/bills") || w.includes("/uploads"))).toEqual([]);
    const bills = await call<unknown[]>(page, "GET", `/houses/${houseId}/bills`);
    expect(bills).toHaveLength(0);
  });

  for (const scheme of ["light", "dark"] as const) {
    test(`EXTRACT-14 "Upload PDF" on the capture sheet reads a text PDF into draft items (${scheme}) [FR-10] [FR-11] [FR-12]`, async ({
      page,
    }, info) => {
      test.setTimeout(120_000);
      await page.emulateMedia({ colorScheme: scheme });
      const houseId = await signedInHouse(page);
      await openCapture(page, houseId);
      const sheet = page.getByRole("dialog", { name: "Snap a receipt" });
      for (const name of [
        "Take a photo",
        "Choose photo or PDF",
        "Upload PDF",
        "Enter items by hand",
      ]) {
        await expect(sheet.getByRole("button", { name })).toBeVisible();
      }
      await page.screenshot({ path: info.outputPath(`capture-sheet-${scheme}.png`) });
      const chooser = page.waitForEvent("filechooser");
      await sheet.getByRole("button", { name: "Upload PDF" }).click();
      await (await chooser).setFiles(receiptFile("r01.pdf"));
      expect(await waitForReading(page)).toBe("review");

      const expected = truth("r01");
      const bill = await billOf(page, billIdFromUrl(page));
      expect(bill.bill.store_name).toBe(expected.store_name);
      expect(bill.bill.receipt_total_cents).toBe(expected.total_cents);
      const score = lineScore(bill.items, expected.items);
      expect(score.right).toBe(score.of); // text PDFs: every line exact
      await expect(page.getByLabel("STORE")).toHaveValue(expected.store_name);
      await expect(page.getByText("*** MATHS CHECKS OUT ***")).toBeVisible();
      await expect(page.getByRole("button", { name: /^Edit / })).toHaveCount(expected.items.length);
    });
  }

  test("EXTRACT-14 a PDF dropped on the capture screen is read like a picked one (desktop drag-and-drop) [FR-10] [FR-11]", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const houseId = await signedInHouse(page);
    await openCapture(page, houseId);
    const b64 = readFileSync(receiptFile("r02.pdf")).toString("base64");
    const dt = await page.evaluateHandle((data) => {
      const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], "walmart.pdf", { type: "application/pdf" }));
      return transfer;
    }, b64);
    await page.dispatchEvent(".kt-dropzone", "dragover", { dataTransfer: dt });
    await expect(page.locator(".kt-dropzone")).toHaveAttribute("data-active", "true");
    await page.dispatchEvent(".kt-dropzone", "drop", { dataTransfer: dt });
    expect(await waitForReading(page)).toBe("review");
    const bill = await billOf(page, billIdFromUrl(page));
    expect(bill.bill.store_name).toBe("Walmart");
    expect(bill.bill.receipt_total_cents).toBe(truth("r02").total_cents);
  });
});

test.describe("photos", () => {
  test("EXTRACT-02 the stored photo has no EXIF and no GPS [FR-10] [FR-18]", async ({ page }) => {
    test.setTimeout(120_000);
    const houseId = await signedInHouse(page);
    await openCapture(page, houseId);
    const original = withExifGps(readFileSync(receiptFile("i01.jpg")));
    expect(original.includes(Buffer.from("GPSLatitude"))).toBe(true);
    await library(page).setInputFiles({
      name: "IMG_0001.jpg",
      mimeType: "image/jpeg",
      buffer: original,
    });
    await page.waitForURL(/\/review$/);
    const billId = billIdFromUrl(page);
    let receipts: { id: string; mime: string; bytes: number }[] = [];
    await expect
      .poll(async () => (receipts = await call(page, "GET", `/bills/${billId}/receipts`)).length, {
        timeout: 60_000,
      })
      .toBe(1);
    const view = await call<{ url: string }>(page, "GET", `/receipts/${receipts[0]!.id}/view`);
    const stored = Buffer.from(await (await page.request.get(view.url)).body());
    expect(stored.length).toBe(receipts[0]!.bytes);
    expect([...stored.subarray(0, 3)]).toEqual([0xff, 0xd8, 0xff]);
    expect(stored.includes(Buffer.from("Exif\0\0", "latin1"))).toBe(false);
    expect(stored.includes(Buffer.from("GPS"))).toBe(false);
    expect(receipts[0]!.mime).toBe("image/jpeg");
  });

  test("EXTRACT-06 a photo that isn't a receipt fails with Try again / Enter by hand, and the file stays [FR-10] [FR-15]", async ({
    page,
  }) => {
    test.setTimeout(240_000);
    const houseId = await signedInHouse(page);
    await openCapture(page, houseId);
    await library(page).setInputFiles({
      name: "wall.png",
      mimeType: "image/png",
      buffer: blankPng(),
    });
    expect(await waitForReading(page)).toBe("failed");
    const billId = billIdFromUrl(page);
    await expect(page.getByText("Couldn't read that one.", { exact: true }).last()).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
    const kept = await call<unknown[]>(page, "GET", `/bills/${billId}/receipts`);
    expect(kept).toHaveLength(1);
    expect((await billOf(page, billId)).items).toHaveLength(0); // nothing invented

    await page.getByRole("button", { name: "Try again" }).click();
    expect(await waitForReading(page)).toBe("failed");
    expect(await call<unknown[]>(page, "GET", `/bills/${billId}/receipts`)).toHaveLength(1); // not uploaded twice

    await page.getByRole("button", { name: "Enter by hand" }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Did we read it right?" }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "+ ADD A MISSED ITEM" })).toBeVisible();
  });
});

// Five photos (one with a printed total that doesn't add up) and one scanned PDF, read in the browser exactly as on a phone (EXTRACT-12).
const OCR_FIXTURES = ["i01", "i02", "i03", "i07", "i09", "i05"] as const;

test.describe("browser OCR", () => {
  for (const id of OCR_FIXTURES) {
    test(`EXTRACT-12 ${id} is read on the device from /ocr/ with no third-party requests; lines and total within the C1 targets [FR-10] [FR-11] [FR-12]`, async ({
      page,
      context,
      baseURL,
    }) => {
      test.setTimeout(300_000);
      const hosts = new Set<string>();
      const ocrAssets = new Set<string>();
      context.on("request", (r) => {
        const url = new URL(r.url());
        if (url.protocol.startsWith("http")) hosts.add(url.hostname);
        if (url.pathname.startsWith("/ocr/")) ocrAssets.add(url.pathname);
      });
      const houseId = await signedInHouse(page);
      await openCapture(page, houseId);
      const file = fixtureFile(id);
      await library(page).setInputFiles(receiptFile(file));
      expect(await waitForReading(page, 280_000)).toBe("review");

      const expected = truth(id);
      const bill = await billOf(page, billIdFromUrl(page));
      const score = lineScore(bill.items, expected.items);
      expect(
        score.right / score.of,
        `${id}: ${score.right}/${score.of} lines`,
      ).toBeGreaterThanOrEqual(0.85);
      expect(bill.bill.receipt_total_cents).toBe(expected.total_cents);

      // Self-hosted: worker, core and model from this origin; storage is the local MinIO.
      expect([...ocrAssets]).toEqual(
        expect.arrayContaining(["/ocr/worker.min.js", "/ocr/lang/eng.traineddata.gz"]),
      );
      expect([...ocrAssets].some((p) => p.startsWith("/ocr/core/tesseract-core"))).toBe(true);
      const app = new URL(baseURL!).hostname;
      for (const host of hosts) expect(["localhost", "127.0.0.1", app]).toContain(host);
    });
  }
});

function fixtureFile(id: string): string {
  for (const ext of [".jpg", ".png", ".pdf"]) {
    try {
      readFileSync(receiptFile(`${id}${ext}`));
      return `${id}${ext}`;
    } catch {
      // try the next extension
    }
  }
  throw new Error(`no fixture file for ${id}`);
}

test.describe("scanned items offered after typing (SYNC-15)", () => {
  async function draftWithTypedItem(page: Page): Promise<string> {
    const houseId = await signedInHouse(page);
    const bill = await call<{ id: string }>(page, "POST", `/houses/${houseId}/bills`, {});
    await call(page, "POST", `/bills/${bill.id}/items`, {
      name: "Typed by hand",
      line_total_cents: 500,
    });
    // The scan lands after the typing (exactly what the browser posts after OCR).
    const ocr = JSON.parse(readFileSync(receiptFile("i01.ocr.json"), "utf8")) as {
      pages: unknown[];
    };
    const out = await call<{ extraction: string }>(page, "POST", `/bills/${bill.id}/ocr`, {
      parts: [{ pages: ocr.pages }],
    });
    expect(out.extraction).toBe("offered");
    return bill.id;
  }

  test("SYNC-15 the banner offers the scan; using it replaces the typed items [FR-14] [FR-15]", async ({
    page,
  }) => {
    const billId = await draftWithTypedItem(page);
    await page.goto(`/bills/${billId}/review`);
    const banner = page.getByRole("region", { name: "Use the scanned items instead?" });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("The scan found 12 items · $94.28");
    await expect(page.getByRole("button", { name: "Edit Typed by hand" })).toBeVisible();
    await banner.getByRole("button", { name: "Use the scanned items" }).click();
    await expect(banner).toBeHidden();
    await expect(page.getByRole("button", { name: "Edit Typed by hand" })).toBeHidden();
    await expect(page.getByRole("button", { name: /^Edit / })).toHaveCount(12);
    await expect(page.getByLabel("STORE")).toHaveValue("ShopRite");
    const bill = await billOf(page, billId);
    expect(bill.scanned_offer).toBeNull();
    expect(bill.items).toHaveLength(12);
  });

  test("SYNC-15 Keep mine dismisses the scan and keeps what was typed [FR-14] [FR-15]", async ({
    page,
  }) => {
    const billId = await draftWithTypedItem(page);
    await page.goto(`/bills/${billId}/review`);
    const banner = page.getByRole("region", { name: "Use the scanned items instead?" });
    await banner.getByRole("button", { name: "Keep mine" }).click();
    await expect(banner).toBeHidden();
    await expect(page.getByRole("button", { name: "Edit Typed by hand" })).toBeVisible();
    const bill = await billOf(page, billId);
    expect(bill.scanned_offer).toBeNull();
    expect(bill.items.map((i) => i.name)).toEqual(["Typed by hand"]);
  });
});
