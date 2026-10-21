/** The echo frame: a gilded picture frame holding one price move and its delayed echo. */
export function LogoMark({ size = 30, title }: { size?: number; title?: string }) {
  return (
    <svg viewBox="0 0 40 40" width={size} height={size} role={title ? "img" : undefined} aria-hidden={title ? undefined : true}>
      {title && <title>{title}</title>}
      <defs>
        <linearGradient id="gilt" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#F3DA9B" />
          <stop offset="0.35" stopColor="#B8873A" />
          <stop offset="0.6" stopColor="#EBCB80" />
          <stop offset="1" stopColor="#8A6326" />
        </linearGradient>
      </defs>
      {/* frame with corner blocks */}
      <rect x="3" y="3" width="34" height="34" rx="1.5" fill="url(#gilt)" />
      <rect x="1" y="1" width="9" height="9" rx="1.5" fill="url(#gilt)" />
      <rect x="30" y="1" width="9" height="9" rx="1.5" fill="url(#gilt)" />
      <rect x="1" y="30" width="9" height="9" rx="1.5" fill="url(#gilt)" />
      <rect x="30" y="30" width="9" height="9" rx="1.5" fill="url(#gilt)" />
      <rect x="7.5" y="7.5" width="25" height="25" fill="#13100D" />
      <rect x="8.5" y="8.5" width="23" height="23" fill="none" stroke="#B8873A" strokeOpacity="0.55" strokeWidth="0.8" />
      {/* the echo: same move, later */}
      <polyline points="13.5,27 18,22 21,24.5 28.5,14.5" fill="none" stroke="#EDE6D8" strokeOpacity="0.38"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {/* the lead */}
      <polyline points="10.5,27 15,22 18,24.5 25.5,14.5" fill="none" stroke="#E9C77F" strokeWidth="2.2"
        strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="25.5" cy="14.5" r="1.6" fill="#E9C77F" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="logo">
      <LogoMark />
      <span className="logo-word">Crypto Latency Lab</span>
    </span>
  );
}
