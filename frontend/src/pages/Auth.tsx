import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { AuthForm } from "../components/AuthForm";
import { useSession } from "../session";

export function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const { me, refresh } = useSession();
  const nav = useNavigate();
  if (me?.user && !me.user.guest) return <Navigate to="/app" replace />;
  return (
    <section className="section narrow">
      <div className="card auth-card">
        <h1>{mode === "login" ? "Welcome back" : "Create your account"}</h1>
        <p className="muted">{mode === "login" ? "Log in to get back to your beats." : "Free to start. No card needed."}</p>
        <AuthForm key={mode} initialMode={mode} onDone={async () => { await refresh(); nav("/app"); }} />
      </div>
    </section>
  );
}

export function ResetPage() {
  const [params] = useSearchParams();
  const { refresh } = useSession();
  const nav = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const token = params.get("token") ?? "";
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.reset(token, password);
      await refresh();
      nav("/app");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setBusy(false);
    }
  };
  return (
    <section className="section narrow">
      <div className="card auth-card">
        <h1>Choose a new password</h1>
        {!token ? (
          <p className="form-error">This reset link is incomplete. Open the link from your email again.</p>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <label>
              New password
              <input type="password" name="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters…" />
            </label>
            {error && <div className="form-error" role="alert">{error}</div>}
            <button className="btn btn-primary btn-block" disabled={busy}>{busy ? "Saving…" : "Save and log in"}</button>
          </form>
        )}
      </div>
    </section>
  );
}
