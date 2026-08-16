// Pre-paint appearance. Loaded as a plain classic script from index.html
// (NOT type="module", NOT defer — either would run after first paint and
// reintroduce the color flash).
//
// It lives in /public rather than inline so the Content-Security-Policy can
// stay `script-src 'self'` with no sha256 hash to re-compute on every edit.
//
// TWO choices are applied here:
//
//   data-palette  which LIGHT palette (pop | neem | turmeric). The ids must
//                 stay in lockstep with the @theme / [data-palette=…] blocks
//                 in src/styles/tokens.css and src/styles/palette-lab.css.
//                 Retired ids (sunset/indigo/forest) fall back to the default
//                 so stale localStorage can't strand anyone.
//
//   data-theme    light or dark, RESOLVED here from the stored preference
//                 (system | light | dark). Dark used to be a bare
//                 prefers-color-scheme media query, which meant a device set
//                 to dark forced dark on the user with no way back to a light
//                 palette. Resolving it in JS keeps tokens.css to one plain
//                 [data-theme="dark"] block instead of a media query plus an
//                 override of that media query.
try {
  var p = localStorage.getItem("ilops_palette");
  document.documentElement.dataset.palette =
    ["pop", "neem", "turmeric"].indexOf(p) >= 0 ? p : "pop";
} catch (e) {
  document.documentElement.dataset.palette = "pop";
}

try {
  var mode = localStorage.getItem("ilops_theme");
  if (mode !== "light" && mode !== "dark") mode = "system";
  var dark =
    mode === "dark" ||
    (mode === "system" &&
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
} catch (e) {
  document.documentElement.dataset.theme = "light";
}
