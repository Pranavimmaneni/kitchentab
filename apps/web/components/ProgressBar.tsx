import { strings } from "@/lib/strings";

export type ProgressBarProps = {
  value: number;
  max: number;
  /** Accessible name; defaults to "Items assigned". */
  label?: string;
  /** Spoken value; defaults to "{value} of {max} items assigned". */
  valueText?: string;
};

/** 14 px outlined bar with a lime fill and a mono "9/12" label (PRD §14B). */
export function ProgressBar({ value, max, label = strings.split.progressLabel, valueText }: ProgressBarProps) {
  const safeMax = Math.max(0, max);
  const clamped = Math.max(0, Math.min(value, safeMax));
  const pct = safeMax === 0 ? 0 : (clamped / safeMax) * 100;
  return (
    <div className="kt-progress">
      <div
        className="kt-progress__track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={safeMax}
        aria-valuenow={clamped}
        aria-valuetext={valueText ?? strings.split.progressText(clamped, safeMax)}
      >
        <div className="kt-progress__fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="kt-progress__label kt-money" aria-hidden="true">
        {clamped}/{safeMax}
      </span>
    </div>
  );
}
