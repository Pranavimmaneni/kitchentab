import type { ReactNode, SVGProps } from "react";

/**
 * Inline stroke icons (PRD §14B): 24×24 viewBox, 2–2.2 px stroke, round caps, currentColor.
 * Decorative by default (aria-hidden); pass `title` to give an icon its own accessible name.
 */
export type IconProps = Omit<SVGProps<SVGSVGElement>, "children"> & {
  size?: number;
  title?: string;
};

function makeIcon(name: string, body: ReactNode, defaultStroke = 2) {
  function Icon({ size = 20, title, strokeWidth = defaultStroke, ...rest }: IconProps) {
    const labelled = Boolean(title);
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden={labelled ? undefined : true}
        role={labelled ? "img" : undefined}
        focusable="false"
        data-icon={name}
        {...rest}
      >
        {title ? <title>{title}</title> : null}
        {body}
      </svg>
    );
  }
  Icon.displayName = `Icon(${name})`;
  return Icon;
}

export const BackIcon = makeIcon("back", <path d="M15 18l-6-6 6-6" />, 2.2);

export const SunIcon = makeIcon(
  "sun",
  <>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </>,
);

export const MoonIcon = makeIcon("moon", <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />);

export const ScanIcon = makeIcon(
  "scan",
  <>
    <path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2" />
    <path d="M8 12h8" />
  </>,
  2.2,
);

export const ReceiptIcon = makeIcon(
  "receipt",
  <>
    <path d="M6 3h12v18l-3-2-3 2-3-2-3 2z" />
    <path d="M9 8h6M9 12h6" />
  </>,
);

export const ScaleIcon = makeIcon(
  "scale",
  <path d="M12 3v18M5 7h14M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z" />,
);

export const HouseIcon = makeIcon(
  "house",
  <>
    <path d="M3 11l9-7 9 7" />
    <path d="M5 10v10h14V10" />
  </>,
);

export const KitchenIcon = makeIcon(
  "kitchen",
  <>
    <rect x="6" y="2.5" width="12" height="19" rx="2" />
    <path d="M6 9.5h12M9 5.5v2M9 12.5v4" />
  </>,
);

export const PencilIcon = makeIcon(
  "pencil",
  <>
    <path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z" />
    <path d="M13.5 6.5l4 4" />
  </>,
);

export const PlusIcon = makeIcon("plus", <path d="M12 5v14M5 12h14" />, 2.2);

export const XIcon = makeIcon("x", <path d="M18 6L6 18M6 6l12 12" />, 2.2);

export const SendIcon = makeIcon(
  "send",
  <>
    <path d="M22 2L11 13" />
    <path d="M22 2l-7 20-4-9-9-4z" />
  </>,
  2.2,
);

export const BellIcon = makeIcon(
  "bell",
  <>
    <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
    <path d="M13.7 21a2 2 0 0 1-3.4 0" />
  </>,
);

export const CheckIcon = makeIcon("check", <path d="M5 12.5l4.5 4.5L19 7.5" />, 2.2);

export const MoreIcon = makeIcon(
  "more",
  <>
    <circle cx="5" cy="12" r="1.2" />
    <circle cx="12" cy="12" r="1.2" />
    <circle cx="19" cy="12" r="1.2" />
  </>,
  2.2,
);

export const LockIcon = makeIcon(
  "lock",
  <>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </>,
);

export const UploadIcon = makeIcon(
  "upload",
  <>
    <path d="M12 15V4M7 9l5-5 5 5" />
    <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
  </>,
);

export const icons = {
  back: BackIcon,
  sun: SunIcon,
  moon: MoonIcon,
  scan: ScanIcon,
  receipt: ReceiptIcon,
  scale: ScaleIcon,
  house: HouseIcon,
  kitchen: KitchenIcon,
  pencil: PencilIcon,
  plus: PlusIcon,
  x: XIcon,
  send: SendIcon,
  bell: BellIcon,
  check: CheckIcon,
  more: MoreIcon,
  lock: LockIcon,
  upload: UploadIcon,
} as const;

export type IconName = keyof typeof icons;
