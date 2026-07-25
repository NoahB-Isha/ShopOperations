import "@fontsource-variable/fraunces/full.css"; // full axes — WONK powers the quirky display type
import "@fontsource-variable/inter";
// era typefaces for the time machine: typewriter past, sci-fi future
import "@fontsource/special-elite";
import "@fontsource/orbitron/500.css";
import "@fontsource/orbitron/700.css";
import "./styles/tokens.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
