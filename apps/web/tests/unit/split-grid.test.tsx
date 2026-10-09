import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SplitGrid, type SplitItem, type SplitMember } from "@/components/SplitGrid";

const members: SplitMember[] = [
  { id: "pranav", name: "Pranav", tag: "YOU", colorIndex: 0 },
  { id: "arjun", name: "Arjun", tag: "ARJ", colorIndex: 1 },
  { id: "meera", name: "Meera", tag: "MEE", colorIndex: 2 },
];
const items: SplitItem[] = [
  { id: "milk", name: "Whole milk", priceCents: 429, note: "$1.43 × 3" },
  { id: "eggs", name: "Eggs", priceCents: 549 },
  { id: "rice", name: "Basmati rice", priceCents: 1499, note: "JUST MEERA" },
];
const assigned = { milk: ["pranav", "arjun", "meera"], eggs: [], rice: ["meera"] };

function box(member: string, item: string): HTMLElement {
  return screen.getByRole("button", { name: `${member} uses ${item}` });
}

function tabStops(): HTMLElement[] {
  return screen.getAllByRole("button").filter((b) => b.tabIndex === 0);
}

function press(key: string, init: Partial<KeyboardEventInit> = {}): void {
  fireEvent.keyDown(document.activeElement!, { key, ...init });
}

describe("SplitGrid", () => {
  it("has exactly one tab stop and arrow keys move it between checkboxes", () => {
    render(<SplitGrid members={members} items={items} assigned={assigned} />);
    expect(screen.getByRole("grid", { name: "Who's eating what" })).toBeTruthy();
    expect(tabStops()).toEqual([box("Pranav", "Whole milk")]);

    box("Pranav", "Whole milk").focus();
    press("ArrowRight");
    expect(document.activeElement).toBe(box("Arjun", "Whole milk"));
    press("ArrowDown");
    expect(document.activeElement).toBe(box("Arjun", "Eggs"));
    press("ArrowLeft");
    expect(document.activeElement).toBe(box("Pranav", "Eggs"));
    press("ArrowUp");
    expect(document.activeElement).toBe(box("Pranav", "Whole milk"));
    press("End");
    expect(document.activeElement).toBe(box("Meera", "Whole milk"));
    press("End", { ctrlKey: true });
    expect(document.activeElement).toBe(box("Meera", "Basmati rice"));
    press("Home");
    expect(document.activeElement).toBe(box("Pranav", "Basmati rice"));

    expect(tabStops()).toEqual([box("Pranav", "Basmati rice")]);
  });

  it("stops at the edges instead of wrapping", () => {
    render(<SplitGrid members={members} items={items} assigned={assigned} />);
    box("Pranav", "Whole milk").focus();
    press("ArrowLeft");
    press("ArrowUp");
    expect(document.activeElement).toBe(box("Pranav", "Whole milk"));
  });

  it("member mode only lets you tick your own column and roves within it", () => {
    render(
      <SplitGrid members={members} items={items} assigned={assigned} mode="member" currentMemberId="arjun" />,
    );
    expect(box("Pranav", "Whole milk")).toHaveProperty("disabled", true);
    expect(box("Arjun", "Whole milk")).toHaveProperty("disabled", false);
    expect(tabStops()).toEqual([box("Arjun", "Whole milk")]);
    box("Arjun", "Whole milk").focus();
    press("ArrowRight");
    expect(document.activeElement).toBe(box("Arjun", "Whole milk"));
    press("ArrowDown");
    expect(document.activeElement).toBe(box("Arjun", "Eggs"));
  });

  it("reports toggles with the next state and only displays totals passed in", () => {
    const onToggle = vi.fn();
    render(
      <SplitGrid
        members={members}
        items={items}
        assigned={assigned}
        onToggle={onToggle}
        totals={[
          { memberId: "pranav", amountCents: 101 },
          { memberId: "arjun", amountCents: 202 },
          { memberId: "meera", amountCents: 30303 },
        ]}
      />,
    );
    fireEvent.click(box("Meera", "Eggs"));
    expect(onToggle).toHaveBeenCalledWith("eggs", "meera", true);
    fireEvent.click(box("Meera", "Whole milk"));
    expect(onToggle).toHaveBeenCalledWith("milk", "meera", false);
    const totals = screen.getByRole("group", { name: "Totals so far" });
    expect(within(totals).getByText("$1.01")).toBeTruthy();
    expect(within(totals).getByText("$2.02")).toBeTruthy();
    expect(within(totals).getByText("$303.03")).toBeTruthy();
    expect(screen.getByText("NOBODY YET")).toBeTruthy();
    expect(screen.getByText("JUST MEERA")).toBeTruthy();
  });

  it("read-only mode has no tab stop and every box is disabled", () => {
    render(<SplitGrid members={members} items={items} assigned={assigned} mode="readonly" />);
    expect(tabStops()).toEqual([]);
    expect(screen.getAllByRole("button").every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
  });

  it("past five members the grid switches to the scrolling layout", () => {
    const six: SplitMember[] = [
      ...members,
      { id: "kabir", name: "Kabir", tag: "KAB", colorIndex: 3 },
      { id: "riya", name: "Riya", tag: "RIY", colorIndex: 4 },
      { id: "sam", name: "Sam", tag: "SAM", colorIndex: 5 },
    ];
    const { container } = render(<SplitGrid members={six} items={items} assigned={assigned} />);
    expect(container.querySelector(".kt-split")!.getAttribute("data-wide")).toBe("true");
    expect(screen.getByRole("group", { name: "Totals so far" }).tabIndex).toBe(0);
  });

  it("shows a busy skeleton while loading", () => {
    render(<SplitGrid members={members} items={[]} assigned={{}} loading />);
    expect(screen.getByRole("grid").getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
  });
});
