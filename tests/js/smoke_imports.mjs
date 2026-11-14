// Imports every web module in dependency order. Linking fails on any missing export or file; running checks
// the top-level code with a stub DOM. Run: node --import ./tests/js/register.mjs tests/js/link_check.mjs
import { installBrowserGlobals } from "./stub_dom.mjs";

installBrowserGlobals();
const modules = ["/state.js", "/util.js", "/sources.js", "/lobby.js", "/scan-table.js", "/player-details.js",
  "/api.js", "/attention.js", "/settings.js", "/app.js", "/table-view.js"];
for (const m of modules) {
  if (m === "/table-view.js") {
    // classic IIFE script: loads without exports; a plain import is enough to run it here
  }
  await import(m);
  console.log("linked", m);
}
