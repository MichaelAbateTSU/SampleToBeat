import "@fontsource-variable/archivo/standard.css";
import "@fontsource-variable/doto";
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
