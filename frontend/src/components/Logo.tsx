import { Link } from "react-router-dom";
import { BRAND } from "../brand";

/** Four bars of a chopped waveform on a dark key. Each bar is one flat colour, amber to ember. */
export function LogoMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#17181a" />
      <rect x="10" y="26" width="8" height="15" rx="4" fill="#ffc233" />
      <rect x="22" y="13" width="8" height="38" rx="4" fill="#ffa02e" />
      <rect x="34" y="24" width="8" height="25" rx="4" fill="#ff7a30" />
      <rect x="46" y="16" width="8" height="33" rx="4" fill="#ff5a36" />
    </svg>
  );
}

export function Logo({ to = "/" }: { to?: string }) {
  return (
    <Link to={to} className="logo" aria-label={`${BRAND} home`} translate="no">
      <LogoMark />
      <span>{BRAND}</span>
    </Link>
  );
}
