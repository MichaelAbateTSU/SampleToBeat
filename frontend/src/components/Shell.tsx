import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { BRAND, SUPPORT_EMAIL } from "../brand";
import { useSession } from "../session";
import { Icon } from "./Icon";
import { Logo } from "./Logo";

function AccountLinks() {
  const { me, askSignup } = useSession();
  const registered = !!me?.user && !me.user.guest;
  if (registered) return <Link className="btn btn-secondary btn-sm" to="/app">Open studio</Link>;
  return (
    <>
      <button className="btn btn-ghost btn-sm" onClick={() => askSignup("Log in to pick up where you left off.", "login")}>Log in</button>
      <Link className="btn btn-primary btn-sm" to="/app/new">Make a beat</Link>
    </>
  );
}

export function SiteShell() {
  return (
    <div className="site">
      <header className="nav">
        <div className="nav-inner">
          <Logo />
          <nav className="nav-links" aria-label="Main">
            <Link to="/#how">How it works</Link>
            <Link to="/#features">Features</Link>
            <Link to="/pricing">Pricing</Link>
            <Link to="/#faq">FAQ</Link>
          </nav>
          <div className="nav-actions"><AccountLinks /></div>
        </div>
      </header>
      <main><Outlet /></main>
      <footer className="footer">
        <div className="footer-inner">
          <div>
            <Logo />
            <p className="muted">Turn any sample into a full instrumental beat.</p>
          </div>
          <nav aria-label="Footer">
            <Link to="/pricing">Pricing</Link>
            <Link to="/terms">Terms</Link>
            <Link to="/privacy">Privacy</Link>
            <a href={`mailto:${SUPPORT_EMAIL}`}>Contact</a>
          </nav>
        </div>
        <div className="footer-inner fineprint">© {new Date().getFullYear()} {BRAND}. You're responsible for the rights to any sample you upload.</div>
      </footer>
    </div>
  );
}

export function AppShell() {
  const { me, askSignup } = useSession();
  const nav = useNavigate();
  const u = me?.usage;
  const registered = !!me?.user && !me.user.guest;
  return (
    <div className="app">
      <header className="nav nav-app">
        <div className="nav-inner">
          <Logo to="/app" />
          <nav className="nav-links" aria-label="App">
            <NavLink to="/app" end>My beats</NavLink>
            <NavLink to="/app/account">Account</NavLink>
          </nav>
          <div className="nav-actions">
            {u && !me?.self_hosted && (
              <Link to="/app/account" className="usage-pill" title="Generations left">
                <Icon name="spark" size={14} /> {u.generations_left} left
              </Link>
            )}
            {!registered && !me?.self_hosted && <button className="btn btn-ghost btn-sm" onClick={() => askSignup(undefined, "login")}>Log in</button>}
            <button className="btn btn-primary btn-sm" onClick={() => nav("/app/new")}><Icon name="plus" size={16} /> New beat</button>
          </div>
        </div>
      </header>
      <main className="app-main"><Outlet /></main>
    </div>
  );
}
