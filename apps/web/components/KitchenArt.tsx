/**
 * The 2.5D fridge and pantry on the Kitchen panels (Kitchen.dc.html, PRD §19.8): viewBox 160×212,
 * 2 px ink strokes, flat fills, a front face plus side and top faces and a flat ground shadow.
 * Decorative and the same whatever is inside. The fridge door swings when the panel is pressed and
 * the magnets and bananas bob; both only when motion is allowed (styles/kitchen.css).
 */

export function FridgeArt() {
  return (
    <svg
      className="kt-art"
      viewBox="0 0 160 212"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="var(--ink)"
      strokeWidth="2"
      strokeLinejoin="round"
    >
      <ellipse cx="80" cy="205" rx="62" ry="5" fill="var(--faint)" stroke="none" />
      <path d="M118 24 L140 12 L140 190 L118 202 Z" fill="var(--side)" />
      <path d="M20 24 L42 12 L140 12 L118 24 Z" fill="var(--top)" />
      <rect x="20" y="24" width="98" height="178" rx="5" fill="var(--cold)" />
      <g className="kt-art__door">
        <rect x="20" y="24" width="98" height="54" rx="5" fill="var(--paper)" />
        <rect x="20" y="78" width="98" height="124" rx="5" fill="var(--paper)" />
        <rect x="104" y="36" width="6" height="30" rx="3" fill="var(--ink)" />
        <rect x="104" y="90" width="6" height="50" rx="3" fill="var(--ink)" />
        <path
          d="M58 41 v14 M51 48 h14 M53 43 l10 10 M63 43 l-10 10"
          stroke="var(--muted)"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <g transform="rotate(-6 56 112)">
          <rect
            x="36"
            y="96"
            width="40"
            height="32"
            fill="var(--ill-note)"
            stroke="var(--food-stroke)"
          />
          <path
            d="M42 106 H70 M42 113 H66 M42 120 H60"
            stroke="var(--food-stroke)"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        </g>
        <rect
          className="kt-art__bob"
          x="80"
          y="150"
          width="15"
          height="15"
          rx="3"
          fill="var(--ill-violet)"
          stroke="var(--food-stroke)"
        />
      </g>
      <circle
        className="kt-art__bob"
        cx="36"
        cy="42"
        r="7"
        fill="var(--ill-lime)"
        stroke="var(--food-stroke)"
      />
      <rect x="28" y="202" width="10" height="5" fill="var(--ink)" />
      <rect x="100" y="202" width="10" height="5" fill="var(--ink)" />
    </svg>
  );
}

export function PantryArt() {
  return (
    <svg
      className="kt-art"
      viewBox="0 0 160 212"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="var(--ink)"
      strokeWidth="2"
      strokeLinejoin="round"
    >
      <ellipse cx="80" cy="205" rx="64" ry="5" fill="var(--faint)" stroke="none" />
      <path d="M126 30 L146 18 L146 192 L126 204 Z" fill="var(--side)" />
      <path d="M14 30 L34 18 L146 18 L126 30 Z" fill="var(--top)" />
      <rect x="14" y="30" width="112" height="174" fill="var(--wall)" />
      <rect x="22" y="52" width="18" height="26" rx="3" fill="var(--ill-red)" />
      <rect x="20" y="45" width="22" height="7" fill="var(--ill-violet)" />
      <rect x="48" y="40" width="26" height="38" fill="var(--ill-orange)" />
      <path d="M52 50 l3 -3 l3 3 l3 -3 l3 3 l3 -3 l3 3" strokeWidth="1.5" />
      <rect x="82" y="56" width="16" height="22" fill="var(--ill-sky)" />
      <rect x="102" y="56" width="16" height="22" fill="var(--ill-lime)" />
      <path d="M14 78 L24 71 L136 71 L126 78 Z" fill="var(--wood-top)" />
      <rect x="14" y="78" width="112" height="7" fill="var(--wood)" />
      <path d="M24 132 L22 104 C22 98 42 98 42 104 L40 132 Z" fill="var(--ill-white)" />
      <rect x="26" y="112" width="12" height="9" fill="var(--ill-magenta)" strokeWidth="1.5" />
      <rect x="50" y="100" width="30" height="32" fill="var(--ill-lavender)" />
      <path d="M50 110 H80" strokeWidth="1.5" />
      <path d="M88 132 L90 106 L106 106 L108 132 Z" fill="var(--ill-brown)" />
      <path d="M90 106 L93 100 H103 L106 106" fill="var(--ill-light-brown)" />
      <path d="M14 132 L24 125 L136 125 L126 132 Z" fill="var(--wood-top)" />
      <rect x="14" y="132" width="112" height="7" fill="var(--wood)" />
      <g className="kt-art__bob">
        <path
          d="M24 176 C30 188 48 188 56 174 C53 173 51 174 49 175 C44 181 33 180 29 168 Z"
          fill="var(--ill-yellow)"
        />
        <path d="M29 168 L28 164 L31 164" strokeWidth="1.5" />
      </g>
      <rect x="66" y="164" width="26" height="22" fill="var(--ill-orange)" />
      <rect x="94" y="160" width="26" height="26" fill="var(--ill-coral)" />
      <path d="M14 186 L24 179 L136 179 L126 186 Z" fill="var(--wood-top)" />
      <rect x="14" y="186" width="112" height="8" fill="var(--wood)" />
    </svg>
  );
}
