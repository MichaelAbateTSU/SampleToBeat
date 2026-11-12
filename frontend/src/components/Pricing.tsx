import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Plan, type PlanId } from "../api";
import { useSession } from "../session";
import { Icon } from "./Icon";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export const money = (n: number) => (Number.isInteger(n) ? usd.format(n).replace(/\.00$/, "") : usd.format(n));

export function planPerks(p: Plan): string[] {
  const best = p.formats.includes("wav32f") ? "WAV up to 32-bit float" : p.formats.includes("wav24") ? "WAV 16/24-bit + MP3" : "MP3 downloads";
  return [
    `${p.generations_per_month.toLocaleString()} generations a month`,
    best,
    p.stems ? "Track stems (zip)" : "",
    p.midi ? "MIDI of drums, 808 and chops" : "",
    p.commercial_license ? "Commercial license for what ChopLab makes" : "Personal-use license",
    p.separation ? "Stem separation for full songs" : "",
    `Samples up to ${p.max_source_minutes} min / ${p.max_upload_mb >= 1024 ? "1 GB" : `${p.max_upload_mb} MB`}`,
    `${p.projects.toLocaleString()} saved projects`,
  ].filter(Boolean);
}

export function useCheckout() {
  const { me, askSignup, guard } = useSession();
  const [busy, setBusy] = useState(false);
  const start = async (plan: PlanId, interval: "monthly" | "yearly") => {
    if (!me?.user || me.user.guest) {
      if (!(await askSignup("Create your account first, then you'll go straight to checkout."))) return;
    }
    setBusy(true);
    const r = await guard(() => api.checkout(plan, interval));
    if (r) window.location.assign(r.url);
    else setBusy(false);
  };
  return { start, busy };
}

export function Pricing({ compact }: { compact?: boolean }) {
  const { plans, me } = useSession();
  const [interval, setInterval] = useState<"monthly" | "yearly">("monthly");
  const { start, busy } = useCheckout();
  const nav = useNavigate();
  if (!plans) return null;
  const current = me?.user?.plan ?? "free";
  const registered = !!me?.user && !me.user.guest;
  return (
    <div className="pricing">
      {!compact && (
        <div className="seg" role="radiogroup" aria-label="Billing interval">
          <button role="radio" aria-checked={interval === "monthly"} className={interval === "monthly" ? "on" : ""} onClick={() => setInterval("monthly")}>Monthly</button>
          <button role="radio" aria-checked={interval === "yearly"} className={interval === "yearly" ? "on" : ""} onClick={() => setInterval("yearly")}>Yearly <span className="save">2 months free</span></button>
        </div>
      )}
      <div className="plans">
        {plans.order.map((id) => {
          const p = plans.plans[id];
          const price = interval === "yearly" ? p.price_yearly / 12 : p.price_monthly;
          const isCurrent = registered && current === id;
          return (
            <div key={id} className={`plan${id === "pro" ? " plan-featured" : ""}`}>
              {id === "pro" && <div className="plan-flag">Most producers pick this</div>}
              <h3>{p.label}</h3>
              <p className="plan-tag">{p.tagline}</p>
              <div className="plan-price">
                <span className="amount">{money(price)}</span>
                <span className="per">{price === 0 ? "forever" : interval === "yearly" ? "a month, billed yearly" : "a month"}</span>
              </div>
              {id === "free" ? (
                <button className="btn btn-secondary btn-block" onClick={() => nav("/app/new")}>{isCurrent ? "Make a beat" : "Start free"}</button>
              ) : isCurrent ? (
                <button className="btn btn-secondary btn-block" disabled>Your plan</button>
              ) : (
                <button className={`btn btn-block ${id === "pro" ? "btn-primary" : "btn-secondary"}`} disabled={busy} onClick={() => start(id, interval)}>
                  Get {p.label}
                </button>
              )}
              <ul className="perks">
                {planPerks(p).map((perk) => (
                  <li key={perk}><Icon name="check" size={16} /> {perk}</li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
      {plans.billing === "off" && <p className="fineprint">Paid plans open soon. Checkout isn't switched on for this site yet.</p>}
    </div>
  );
}
