import "@fontsource-variable/inter";
import "@fontsource-variable/space-grotesk";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { PlayerProvider } from "./player";
import { SessionProvider } from "./session";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <PlayerProvider>
        <SessionProvider>
          <App />
        </SessionProvider>
      </PlayerProvider>
    </BrowserRouter>
  </StrictMode>,
);
