import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, type Capabilities, type Me, type PlanId, type Plans } from "./api";
import { AuthModal } from "./components/AuthModal";
import { UpgradeModal } from "./components/UpgradeModal";

interface UpgradePrompt { message: string; planNeeded: PlanId }

interface Session {
  me: Me | null;
  plans: Plans | null;
  caps: Capabilities | null;
  loading: boolean;
  refresh: () => Promise<void>;
  /** Opens the sign-up dialog; resolves true once the visitor has an account. */
  askSignup: (reason?: string, mode?: "signup" | "login") => Promise<boolean>;
  askUpgrade: (prompt: UpgradePrompt) => void;
  /** Runs an API action; if it hits the sign-up wall or a plan limit, shows the right dialog (and retries after sign-up). */
  guard: <T>(action: () => Promise<T>) => Promise<T | undefined>;
  toast: (message: string, kind?: "info" | "error") => void;
}

const Ctx = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [plans, setPlans] = useState<Plans | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [auth, setAuth] = useState<{ reason?: string; mode: "signup" | "login" } | null>(null);
  const [upgrade, setUpgrade] = useState<UpgradePrompt | null>(null);
  const [toasts, setToasts] = useState<{ id: number; message: string; kind: "info" | "error" }[]>([]);
  const authResolve = useRef<((ok: boolean) => void) | null>(null);

  const refresh = useCallback(async () => {
    try {
      setMe(await api.me());
    } catch {
      /* offline: keep what we have */
    }
  }, []);

  useEffect(() => {
    Promise.all([refresh(), api.plans().then(setPlans), api.capabilities().then(setCaps)])
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [refresh]);

  const toast = useCallback((message: string, kind: "info" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, kind }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);

  const askSignup = useCallback((reason?: string, mode: "signup" | "login" = "signup") => {
    setAuth({ reason, mode });
    return new Promise<boolean>((resolve) => {
      authResolve.current = resolve;
    });
  }, []);

  const closeAuth = useCallback(
    (ok: boolean, next?: Me) => {
      if (next) setMe(next);
      setAuth(null);
      authResolve.current?.(ok);
      authResolve.current = null;
    },
    [],
  );

  const guard = useCallback(
    async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
      try {
        return await action();
      } catch (e) {
        if (e instanceof ApiError && e.code === "signup_required") {
          if (await askSignup(e.message)) return guard(action);
          return undefined;
        }
        if (e instanceof ApiError && e.code === "upgrade_required") {
          setUpgrade({ message: e.message, planNeeded: e.planNeeded ?? "pro" });
          return undefined;
        }
        toast(e instanceof Error ? e.message : "Something went wrong.", "error");
        return undefined;
      }
    },
    [askSignup, toast],
  );

  const value = useMemo<Session>(
    () => ({ me, plans, caps, loading, refresh, askSignup, askUpgrade: setUpgrade, guard, toast }),
    [me, plans, caps, loading, refresh, askSignup, guard, toast],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {auth && <AuthModal reason={auth.reason} initialMode={auth.mode} onDone={(m) => closeAuth(true, m)} onClose={() => closeAuth(false)} />}
      {upgrade && <UpgradeModal prompt={upgrade} onClose={() => setUpgrade(null)} />}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            {t.message}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
