import { useNavigate } from "react-router-dom";
import type { PlanId } from "../api";
import { useSession } from "../session";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { money, planPerks, useCheckout } from "./Pricing";

export function UpgradeModal({ prompt, onClose }: { prompt: { message: string; planNeeded: PlanId }; onClose: () => void }) {
  const { plans } = useSession();
  const nav = useNavigate();
  const { start, busy } = useCheckout();
  const plan = plans?.plans[prompt.planNeeded];
  if (!plan) return null;
  return (
    <Modal title={`Unlock with ${plan.label}`} onClose={onClose}>
      <p className="modal-lead">{prompt.message}</p>
      <ul className="perks">
        {planPerks(plan).slice(0, 5).map((p) => (
          <li key={p}><Icon name="check" size={16} /> {p}</li>
        ))}
      </ul>
      <button className="btn btn-primary btn-block" disabled={busy} onClick={() => start(prompt.planNeeded, "monthly")}>
        {busy ? "Opening checkout…" : `Get ${plan.label} for ${money(plan.price_monthly)} a month`}
      </button>
      <button className="btn btn-ghost btn-block" onClick={() => { onClose(); nav("/pricing"); }}>Compare all plans</button>
    </Modal>
  );
}
