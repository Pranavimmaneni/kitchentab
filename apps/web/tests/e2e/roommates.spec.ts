import { expect, test } from "./fixtures/roommates";

test.describe("roommates fixture", () => {
  test("three isolated roommates on alternating phones converge on the gallery", async ({
    roommates,
  }) => {
    const house = await roommates(3);
    expect(house.mates.map((m) => m.device)).toEqual(["iPhone 14", "Pixel 7", "iPhone 14"]);

    await Promise.all(house.pages.map((page) => page.goto("/dev/components")));

    // Roommates 0 and 2 switch to dark; roommate 1 stays light. Each context has its own storage.
    for (const mate of [house.mates[0]!, house.mates[2]!]) {
      await mate.page
        .locator("header")
        .first()
        .getByRole("button", { name: "Switch to dark mode" })
        .click();
    }

    await house.expectEverywhere(
      async (page) => {
        await expect(page.getByRole("heading", { level: 1, name: "Components" })).toBeVisible();
      },
      { within: 2000 },
    );
    await house.expectEverywhere(async (page, mate) => {
      const expected = mate.index === 1 ? null : "dark";
      expect(await page.locator("html").getAttribute("data-theme")).toBe(expected);
    });
    const saved = await Promise.all(
      house.pages.map((p) => p.evaluate(() => localStorage.getItem("kt-theme"))),
    );
    expect(saved).toEqual(["dark", null, "dark"]);
  });

  test("expectEverywhere fails when one roommate never converges", async ({ roommates }) => {
    const house = await roommates(2);
    await Promise.all(house.pages.map((page) => page.goto("/dev/components")));
    await house.mates[0]!.page.locator("header")
      .first()
      .getByRole("button", { name: "Switch to dark mode" })
      .click();
    await expect(
      house.expectEverywhere(
        async (page) => {
          expect(await page.locator("html").getAttribute("data-theme")).toBe("dark");
        },
        { within: 500 },
      ),
    ).rejects.toThrow();
  });
});
