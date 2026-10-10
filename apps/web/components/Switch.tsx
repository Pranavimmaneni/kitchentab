"use client";

export type SwitchProps = {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
  describedBy?: string;
};

/** On/off switch (Fridge.dc.html sharing switch): `role=switch`, 56×32 inside a 44 px target. */
export function Switch({ checked, onChange, label, disabled = false, describedBy }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      className="kt-switch"
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="kt-switch__track" aria-hidden="true">
        <span className="kt-switch__knob" />
      </span>
    </button>
  );
}
