import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { SegmentedControl } from "@/components/SegmentedControl";

type Pref = "system" | "light" | "dark";

function Harness() {
  const [value, setValue] = useState<Pref>("system");
  return (
    <SegmentedControl
      label="Appearance"
      value={value}
      onChange={setValue}
      options={[
        { value: "system", label: "System" },
        { value: "light", label: "Light" },
        { value: "dark", label: "Dark" },
      ]}
    />
  );
}

function selected(): string | undefined {
  return (
    screen.getAllByRole("tab").find((t) => t.getAttribute("aria-selected") === "true")
      ?.textContent ?? undefined
  );
}

describe("SegmentedControl", () => {
  it("is a labelled tablist with one selected, focusable tab", () => {
    render(<Harness />);
    expect(screen.getByRole("tablist", { name: "Appearance" })).toBeTruthy();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1]);
  });

  it("arrow keys select and focus the next option, wrapping at the ends", () => {
    render(<Harness />);
    screen.getByRole("tab", { name: "System" }).focus();
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(selected()).toBe("Light");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Light" }));
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(selected()).toBe("System");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowLeft" });
    expect(selected()).toBe("Dark");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Dark" }));
  });

  it("Home and End jump to the first and last option", () => {
    render(<Harness />);
    screen.getByRole("tab", { name: "System" }).focus();
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(selected()).toBe("Dark");
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    expect(selected()).toBe("System");
  });

  it("click selects", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("tab", { name: "Dark" }));
    expect(selected()).toBe("Dark");
    expect(screen.getByRole("tab", { name: "Dark" }).tabIndex).toBe(0);
  });
});
