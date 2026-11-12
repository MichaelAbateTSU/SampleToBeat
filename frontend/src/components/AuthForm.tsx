import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type Me } from "../api";

type Mode = "signup" | "login" | "forgot";

/** Sign up / log in / request a reset link. Used both in the modal and on the standalone pages. */
export function AuthForm({ initialMode = "signup", onDone }: { initialMode?: "signup" | "login"; onDone: (me: Me) => void }) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "forgot") {
        await api.forgot(email);
        setSent(true);
      } else {
        onDone(await (mode === "signup" ? api.register(email, password) : api.login(email, password)));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  if (sent) {
    return (
      <div className="auth-form">
        <p>If an account exists for <strong>{email}</strong>, a reset link is on its way. It expires in an hour.</p>
        <button className="btn btn-ghost" onClick={() => { setSent(false); setMode("login"); }}>Back to log in</button>
      </div>
    );
  }

  return (
    <form className="auth-form" onSubmit={submit}>
      <label>
        Email
        <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
      </label>
      {mode !== "forgot" && (
        <label>
          Password
          <input type="password" autoComplete={mode === "signup" ? "new-password" : "current-password"} required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={mode === "signup" ? "At least 8 characters" : "Your password"} />
        </label>
      )}
      {error && <div className="form-error" role="alert">{error}</div>}
      <button className="btn btn-primary btn-block" disabled={busy}>
        {busy ? "One moment…" : mode === "signup" ? "Create free account" : mode === "login" ? "Log in" : "Email me a reset link"}
      </button>
      <div className="auth-switch">
        {mode === "signup" && <>Already have an account? <button type="button" className="link" onClick={() => setMode("login")}>Log in</button></>}
        {mode === "login" && (
          <>
            <button type="button" className="link" onClick={() => setMode("forgot")}>Forgot password?</button>
            <span> · </span>
            <button type="button" className="link" onClick={() => setMode("signup")}>Create an account</button>
          </>
        )}
        {mode === "forgot" && <button type="button" className="link" onClick={() => setMode("login")}>Back to log in</button>}
      </div>
      {mode === "signup" && (
        <p className="fineprint">By creating an account you agree to the <Link to="/terms" target="_blank">Terms</Link> and <Link to="/privacy" target="_blank">Privacy Policy</Link>.</p>
      )}
    </form>
  );
}
