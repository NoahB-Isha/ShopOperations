import "@fontsource-variable/fraunces/full.css"; // full axes — WONK powers the quirky display type
import "@fontsource-variable/inter";
import "./styles/tokens.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { watchSystemTheme } from "./theme";

// "Match my device" should keep matching it — a phone that flips at sunset
// flips the app with it, no reload. (The initial paint happens in
// public/palette.js, before React exists.)
watchSystemTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
