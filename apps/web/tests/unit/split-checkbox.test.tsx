import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { SPLIT_TILTS, SplitCheckbox, checkboxTilt } from "@/components/SplitCheckbox";

function Controlled({ onToggle }: { onToggle?: () => void }) {
  const [on, setOn] = useState(false);
  return (
    <SplitCheckbox
      memberName="Arjun"
      itemName="Whole milk"
      initial="A"
      colorIndex={1}
      checked={on}
      row={2}
      col={1}
      onToggle={() => {
        setOn((v) => !v);
        onToggle?.();
      }}
    />
  );
}

describe("SplitCheckbox", () => {
  it("is a toggle button labelled '{Name} uses {Item}'", () => {
    render(<Controlled />);
    const box = screen.getByRole("button", { name: "Arjun uses Whole milk" });
    expect(box).toHaveProperty("type", "button");
    expect(box.getAttribute("aria-pressed")).toBe("false");
  });

  it("toggles aria-pressed on click and shows the stamped initial", () => {
    render(<Controlled />);
    const box = screen.getByRole("button", { name: "Arjun uses Whole milk" });
    fireEvent.click(box);
    expect(box.getAttribute("aria-pressed")).toBe("true");
    expect(box.textContent).toBe("A");
    fireEvent.click(box);
    expect(box.getAttribute("aria-pressed")).toBe("false");
    expect(box.textContent).toBe("");
  });

  it("Space toggles exactly once: keydown toggles, the native keyup activation is cancelled", () => {
    const onToggle = vi.fn();
    render(<Controlled onToggle={onToggle} />);
    const box = screen.getByRole("button", { name: "Arjun uses Whole milk" });
    box.focus();
    const downNotCancelled = fireEvent.keyDown(box, { key: " ", code: "Space" });
    const upNotCancelled = fireEvent.keyUp(box, { key: " ", code: "Space" });
    expect(downNotCancelled).toBe(false);
    expect(upNotCancelled).toBe(false);
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(box.getAttribute("aria-pressed")).toBe("true");
  });

  it("does not toggle when disabled (not your column)", () => {
    const onToggle = vi.fn();
    render(
      <SplitCheckbox
        memberName="Kabir"
        itemName="Eggs"
        initial="K"
        colorIndex={3}
        checked={false}
        disabled
        row={0}
        col={3}
        onToggle={onToggle}
      />,
    );
    const box = screen.getByRole("button", { name: "Kabir uses Eggs" });
    expect(box).toHaveProperty("disabled", true);
    fireEvent.keyDown(box, { key: " " });
    fireEvent.click(box);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it("marks pending cells (50% opacity) until the server acknowledges", () => {
    render(
      <SplitCheckbox
        memberName="Meera"
        itemName="Eggs"
        initial="M"
        colorIndex={2}
        checked
        pending
        row={0}
        col={2}
      />,
    );
    expect(screen.getByRole("button").getAttribute("data-pending")).toBe("true");
  });

  it("picks the tilt deterministically from [-4, 3, -2, 4] by (row + col) % 4", () => {
    expect(SPLIT_TILTS).toEqual([-4, 3, -2, 4]);
    for (let row = 0; row < 12; row++) {
      for (let col = 0; col < 8; col++) {
        expect(checkboxTilt(row, col)).toBe(SPLIT_TILTS[(row + col) % 4]);
      }
    }
    const props = {
      memberName: "Pranav",
      itemName: "Rice",
      initial: "P",
      colorIndex: 0,
      checked: true,
      row: 1,
      col: 2,
    };
    const { container, rerender } = render(<SplitCheckbox {...props} />);
    const stamp = () => container.querySelector<HTMLElement>(".kt-split-box__box")!;
    expect(stamp().style.getPropertyValue("--tilt")).toBe("4deg");
    rerender(<SplitCheckbox {...props} />);
    expect(stamp().style.getPropertyValue("--tilt")).toBe("4deg");
  });
});
