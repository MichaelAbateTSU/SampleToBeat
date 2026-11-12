import { useEffect } from "react";
import { Route, Routes, useLocation } from "react-router-dom";
import { AppShell, SiteShell } from "./components/Shell";
import { Account } from "./pages/Account";
import { AuthPage, ResetPage } from "./pages/Auth";
import { Create } from "./pages/Create";
import { Landing } from "./pages/Landing";
import { Privacy, Terms } from "./pages/Legal";
import { Library } from "./pages/Library";
import { NotFound } from "./pages/NotFound";
import { PricingPage } from "./pages/PricingPage";
import { ProjectPage } from "./pages/Project";

function ScrollToTop() {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (hash) document.querySelector(hash)?.scrollIntoView({ behavior: "smooth" });
    else window.scrollTo(0, 0);
  }, [pathname, hash]);
  return null;
}

export function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route element={<SiteShell />}>
          <Route path="/" element={<Landing />} />
          <Route path="/pricing" element={<PricingPage />} />
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/signup" element={<AuthPage mode="signup" />} />
          <Route path="/reset" element={<ResetPage />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="*" element={<NotFound />} />
        </Route>
        <Route path="/app" element={<AppShell />}>
          <Route index element={<Library />} />
          <Route path="new" element={<Create />} />
          <Route path="p/:id" element={<ProjectPage />} />
          <Route path="account" element={<Account />} />
        </Route>
      </Routes>
    </>
  );
}
