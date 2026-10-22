/**
 * Likely Profits — the house mark.
 * A minted gold coin: a reeded bezel like a watch's, a hairline inner ring, a four-point star at the
 * crown, and an L·P ligature cut like a Didone capital — heavy stems, hairline serifs, the P rising
 * through the L's foot. The wordmark is set in wide-tracked capitals with a gilt sheen.
 */
const TICKS = Array.from({ length: 72 }, (_, i) => {
  const a = (i / 72) * Math.PI * 2;
  const long = i % 6 === 0;
  const r1 = long ? 15.9 : 16.7, r2 = 17.7;
  return [20 + r1 * Math.sin(a), 20 - r1 * Math.cos(a), 20 + r2 * Math.sin(a), 20 - r2 * Math.cos(a), long] as const;
});

// The ligature as filled shapes, so the strokes have real thick/thin contrast.
const L = "M13.1 11.1 H15.5 V27.8 H25.5 V25.9 H26.1 V28.6 H13.1 Z M11.7 10.6 H16.9 V11.1 H11.7 Z";
const P_STEM = "M19.3 9.7 H21.6 V30.1 H19.3 Z M17.8 9.2 H21.6 V9.7 H17.8 Z M17.6 30.1 H23.3 V30.6 H17.6 Z";
const P_BOWL = "M21.6 9.2 H24.1 C27.7 9.2 29.4 11.6 29.4 14.55 C29.4 17.5 27.7 19.9 24.1 19.9 H21.6 V19.1 H23.7 " +
  "C25.95 19.1 26.75 17.2 26.75 14.55 C26.75 11.9 25.95 10 23.7 10 H21.6 Z";

export function LogoMark({ size = 34, title, id = "lp" }: { size?: number; title?: string; id?: string }) {
  const g = `${id}-gold`, s = `${id}-face`, h = `${id}-hi`;
  return (
    <svg className="logo-mark" viewBox="0 0 40 40" width={size} height={size} role={title ? "img" : undefined} aria-hidden={title ? undefined : true}>
      {title && <title>{title}</title>}
      <defs>
        <linearGradient id={g} x1="0.12" y1="0" x2="0.88" y2="1">
          <stop offset="0" stopColor="#FFF1C9" />
          <stop offset="0.22" stopColor="#E3BE74" />
          <stop offset="0.48" stopColor="#8A6427" />
          <stop offset="0.7" stopColor="#F0D18E" />
          <stop offset="1" stopColor="#9A7231" />
        </linearGradient>
        <radialGradient id={s} cx="0.34" cy="0.24" r="0.85">
          <stop offset="0" stopColor="#2C2318" />
          <stop offset="0.65" stopColor="#120E0A" />
          <stop offset="1" stopColor="#080605" />
        </radialGradient>
        <linearGradient id={h} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#fff" stopOpacity="0" />
          <stop offset="0.5" stopColor="#fff" stopOpacity="0.55" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </linearGradient>
        <clipPath id={`${id}-clip`}><circle cx="20" cy="20" r="19.2" /></clipPath>
      </defs>
      <circle cx="20" cy="20" r="19.2" fill={`url(#${s})`} />
      <circle cx="20" cy="20" r="19.2" fill="none" stroke={`url(#${g})`} strokeWidth="1.15" />
      <g stroke={`url(#${g})`} strokeLinecap="round">
        {TICKS.map(([x1, y1, x2, y2, long], i) => (
          <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={long ? 0.55 : 0.26} opacity={long ? 0.95 : 0.55} />
        ))}
      </g>
      <circle cx="20" cy="20" r="15.3" fill="none" stroke={`url(#${g})`} strokeWidth="0.42" opacity="0.9" />
      <circle cx="20" cy="20" r="14.6" fill="none" stroke={`url(#${g})`} strokeWidth="0.18" opacity="0.5" />
      <path d="M20 1.5 L20.7 3.6 L22.8 4.3 L20.7 5 L20 7.1 L19.3 5 L17.2 4.3 L19.3 3.6 Z" fill={`url(#${g})`} />
      <g fill={`url(#${g})`}>
        <path d={L} />
        <path d="M18.7 27.2 H22.2 V29.2 H18.7 Z" fill="#100C09" />{/* the P passes through the L's foot */}
        <path d={P_STEM} />
        <path d={P_BOWL} />
      </g>
      {/* a slow glint across the coin (CSS animates it; static with reduced motion) */}
      <g clipPath={`url(#${id}-clip)`}>
        <rect className="logo-glint" x="-14" y="-4" width="9" height="48" fill={`url(#${h})`} transform="rotate(24 20 20)" />
      </g>
    </svg>
  );
}

export function Logo() {
  return (
    <span className="logo">
      <LogoMark />
      <span className="logo-word">
        <span className="lw-main">Likely Profits</span>
        <span className="lw-sub">Paris · MMXXVI</span>
      </span>
    </span>
  );
}
