import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Pricing } from "../components/Pricing";
import { useSession } from "../session";

export function Account() {
  const { me, refresh, guard, askSignup } = useSession();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [confirm, setConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);
  if (!me) return <div className="skeleton tall" aria-busy="true" aria-label="Loading account" />;
  const u = me.user;
  const registered = !!u && !u.guest;
  const usage = me.usage;
  const paid = registered && u.plan !== "free";
  const pct = usage ? Math.min(100, (usage.generations_used / Math.max(1, usage.generations_limit)) * 100) : 0;

  return (
    <div className="account">
      <h1>Account</h1>
      {params.get("upgraded") && <div className="banner"><Icon name="check" /> You're on {me.plan.label}. Go make something.</div>}

      <div className="card">
        <div className="section-row">
          <div>
            <h2 className="card-title">{me.self_hosted ? "Self-hosted" : `${me.plan.label} plan`}</h2>
            <p className="muted">
              {me.self_hosted ? "Everything is unlocked on your own machine." : registered ? u.email : "You're trying ChopLab as a guest."}
              {paid && u.current_period_end && <>, {u.cancel_at_period_end ? "ends" : "renews"} {new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(u.current_period_end * 1000)}</>}
            </p>
          </div>
          {!registered && !me.self_hosted && <button className="btn btn-primary" onClick={() => askSignup("Create a free account to keep your beats and download them.")}>Create free account</button>}
          {paid && me.billing !== "off" && (
            <button className="btn btn-secondary" onClick={async () => { const r = await guard(() => api.portal()); if (r) window.location.assign(r.url); }}>Manage billing</button>
          )}
        </div>
        {usage && !me.self_hosted && (
          <div className="usage">
            <div className="usage-row"><span>Generations {usage.period === "lifetime" ? "as a guest" : "this month"}</span><span className="num">{usage.generations_used} of {usage.generations_limit.toLocaleString()}</span></div>
            <div className="progress-bar" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label="Generations used"><span style={{ transform: `scaleX(${pct / 100})` }} /></div>
          </div>
        )}
      </div>

      {!me.self_hosted && u?.plan !== "studio" && (
        <div className="card">
          <h2 className="card-title">{paid ? "Need more room?" : "Upgrade"}</h2>
          <Pricing />
        </div>
      )}

      {registered && (
        <div className="card">
          <h2 className="card-title">Session</h2>
          <button className="btn btn-secondary" onClick={async () => { await api.logout(); await refresh(); nav("/"); }}>Log out</button>
        </div>
      )}

      {registered && (
        <div className="card danger">
          <h2 className="card-title">Delete account</h2>
          <p className="muted">Permanently removes your account, uploads, beats and exports, and cancels any subscription. This can't be undone.</p>
          <div className="row wrap">
            <input name="confirm-delete" autoComplete="off" spellCheck={false} value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Type delete to confirm…" aria-label="Type delete to confirm" />
            <button className="btn btn-danger" disabled={confirm.trim().toLowerCase() !== "delete" || deleting} onClick={async () => {
              setDeleting(true);
              const ok = await guard(async () => { await api.deleteAccount(); return true; });
              if (ok) { await refresh(); nav("/"); } else setDeleting(false);
            }}>Delete everything</button>
          </div>
        </div>
      )}
    </div>
  );
}
