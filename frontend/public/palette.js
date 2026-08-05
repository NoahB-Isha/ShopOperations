// Pre-paint palette application. Loaded as a plain classic script from
// index.html (NOT type="module", NOT defer — either would run after first
// paint and reintroduce the color flash).
//
// It lives in /public rather than inline so the Content-Security-Policy can
// stay `script-src 'self'` with no sha256 hash to re-compute on every edit.
//
// The palette ids here must stay in lockstep with the @theme /
// [data-palette=…] blocks in src/styles/tokens.css and src/styles/palette-lab.css.
// Retired ids (sunset/indigo/forest) fall back to the default so stale
// localStorage can't strand anyone.
try {
  var p = localStorage.getItem("ilops_palette");
  document.documentElement.dataset.palette =
    ["pop", "neem", "turmeric"].indexOf(p) >= 0 ? p : "pop";
} catch (e) {
  document.documentElement.dataset.palette = "pop";
}
