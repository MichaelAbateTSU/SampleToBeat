import { Link } from "react-router-dom";
import { BRAND } from "../brand";

export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id="lm" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffc233" />
          <stop offset="1" stopColor="#ff5a36" />
        </linearGradient>
      </defs>
      <g fill="url(#lm)">
        <rect x="6" y="24" width="9" height="16" rx="4.5" />
        <rect x="20" y="11" width="9" height="42" rx="4.5" />
        <rect x="36" y="23" width="9" height="28" rx="4.5" />
        <rect x="50" y="15" width="9" height="36" rx="4.5" />
      </g>
    </svg>
  );
}

export function Logo({ to = "/" }: { to?: string }) {
  return (
    <Link to={to} className="logo" aria-label={`${BRAND} home`}>
      <LogoMark />
      <span>{BRAND}</span>
    </Link>
  );
}
