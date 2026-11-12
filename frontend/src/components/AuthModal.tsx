import type { Me } from "../api";
import { AuthForm } from "./AuthForm";
import { Modal } from "./Modal";

export function AuthModal({ reason, initialMode, onDone, onClose }: { reason?: string; initialMode: "signup" | "login"; onDone: (me: Me) => void; onClose: () => void }) {
  return (
    <Modal title={initialMode === "login" ? "Welcome back" : "Save your beat"} onClose={onClose}>
      <p className="modal-lead">{reason ?? "Create a free account to keep your beats and download them."}</p>
      <AuthForm initialMode={initialMode} onDone={onDone} />
    </Modal>
  );
}
