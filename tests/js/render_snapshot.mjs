// Renders the source-related HTML for the fixtures from the ES modules in web/ and prints JSON on stdout.
// Usage: node --import ./tests/js/register.mjs tests/js/render_snapshot.mjs --new
// tests/golden/frontend_render_golden.json was generated from the single-file app.js before the module split,
// and must keep matching this output (the uid attributes are absent for these fixtures).
import { FIXTURES, LOBBY } from "./fixtures.mjs";


function makeStub() {
  const target = function stub() {};
  const proxy = new Proxy(target, {
    get(t, k) {
      if (k === "then" || typeof k === "symbol") return undefined;
      if (k in t) return t[k];
      t[k] = makeStub();
      return t[k];
    },
    set(t, k, v) { t[k] = v; return true; },
    apply() { return makeStub(); },
    construct() { return makeStub(); },
  });
  return proxy;
}

function renderAll(snap) {
  const out = {};
  for (const [name, p] of Object.entries(FIXTURES)) {
    snap.state.playerTabs = new Map();
    out[`details:${name}`] = snap.renderPlayerDetailsRow(p);
    out[`strip:${name}`] = snap.renderSourceStrip(p);
    out[`chips:${name}`] = snap.sourceChipsFor(p);
    out[`hasdata:${name}`] = { sources: snap.playerDataSources(p), any: snap.playerHasAnyData(p) };
  }
  out["tabs"] = snap.tabs();
  snap.renderLobbySummary(LOBBY);
  out["lobby:risk"] = snap.els.lobbyRisk.innerHTML;
  out["lobby:sources"] = snap.els.lobbySources.innerHTML;
  return out;
}

async function runNew() {
  // Browser-style globals first, then the modules through the "/x.js" resolve hook (register.mjs).
  const { installBrowserGlobals } = await import("./stub_dom.mjs");
  installBrowserGlobals();
  const S = await import("/state.js");
  const SRC = await import("/sources.js");
  const P = await import("/player-details.js");
  const L = await import("/lobby.js");
  return {
    renderPlayerDetailsRow: P.renderPlayerDetailsRow,
    renderSourceStrip: SRC.renderSourceStrip,
    renderLobbySummary: L.renderLobbySummary,
    playerDataSources: SRC.playerDataSources,
    playerHasAnyData: SRC.playerHasAnyData,
    sourceChipsFor: SRC.sourceChipsFor,
    // Tabs of a player with every source present, as {key, label} (the old constant listed exactly these).
    tabs: () => P.tabsFor(FIXTURES.full_mix).map(({ key, label }) => ({ key, label })),
    state: S.state,
    els: S.els,
  };
}

const args = process.argv.slice(2);
if (args[0] === "--new") {
  process.stdout.write(JSON.stringify(renderAll(await runNew()), null, 2) + "\n");
} else {
  console.error("usage: node --import ./tests/js/register.mjs tests/js/render_snapshot.mjs --new");
  process.exit(2);
}
