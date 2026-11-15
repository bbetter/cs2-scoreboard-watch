// Plain Node test (no framework) for web/sources.js and the player tabs/chips built from it.
// Run: node --import ./tests/js/register.mjs tests/js/sources_test.mjs   (exit code 1 on any failure)
import assert from "node:assert/strict";
import { playerDataSources, playerHasAnyData, sourceChipsFor, sourceTabsFor, renderSourceStrip } from "/sources.js";
import { tabsFor, renderPlayerDetailsRow } from "/player-details.js";
import { state } from "/state.js";

// sources.js must import without any browser global.
assert.equal(typeof globalThis.document, "undefined", "test must run without a DOM");

const SID = "76561198000000001";
const base = (over = {}) => ({ nick: "p", steam: { steamid64: SID, profile_name: "p" }, risk_level: "low", ...over });
const chips = (p) => Object.fromEntries(sourceChipsFor(p).map((c) => [c.label, c.state]));
const stateOf = (p, label) => sourceChipsFor(p).find((c) => c.label === label)?.state;

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("empty player: every chip is 'none', nothing counts as data", () => {
  const p = base({ steam: {} });
  assert.deepEqual(chips(p), { Steam: "none", Leetify: "none", CSTracker: "none", "Steam CS2": "none" });
  assert.deepEqual(sourceChipsFor(p).filter((c) => c.retry).map((c) => c.retry), ["leetify", "cstracker"]);
  assert.deepEqual(playerDataSources(p), { leetify: false, cstracker: false, cs2: false });
  assert.equal(playerHasAnyData(p), false);
});

test("steam chip: rate limited, loaded by SteamID or profile name, none otherwise", () => {
  assert.equal(stateOf(base({ steam: { lookup_source: "rate_limited" } }), "Steam"), "rate");
  assert.equal(stateOf(base({ steam: { profile_name: "x" } }), "Steam"), "loaded");
  assert.equal(stateOf(base({ steam: {} }), "Steam"), "none");
});

test("leetify only: loaded chip, counts as data, retry offered", () => {
  const p = base({ steam: { leetify: { aim: 81 } } });
  assert.equal(stateOf(p, "Leetify"), "loaded");
  assert.equal(playerDataSources(p).leetify, true);
  assert.equal(playerHasAnyData(p), true);
  assert.equal(sourceChipsFor(p).find((c) => c.label === "Leetify").retry, "leetify");
});

test("empty leetify object: chip is loaded (truthy) but it is NOT counted as data", () => {
  const p = base({ steam: { leetify: {} } });
  assert.equal(stateOf(p, "Leetify"), "loaded");
  assert.equal(playerDataSources(p).leetify, false);
  assert.equal(playerHasAnyData(p), false);
});

test("cstracker states: ok loaded + counted, blocked, not_found, error, rate_limited -> none", () => {
  const cs = (status) => base({ steam: { extra_stats: { cstracker: { status } } } });
  assert.equal(stateOf(cs("ok"), "CSTracker"), "loaded");
  assert.equal(playerDataSources(cs("ok")).cstracker, true);
  assert.equal(stateOf(cs("blocked"), "CSTracker"), "blocked");
  assert.equal(playerDataSources(cs("blocked")).cstracker, false);
  assert.equal(stateOf(cs("not_found"), "CSTracker"), "none");
  assert.equal(stateOf(cs("error"), "CSTracker"), "error");
  assert.equal(stateOf(cs("rate_limited"), "CSTracker"), "none");  // unchanged historical behaviour
});

test("cs2 stats only: Steam CS2 chip loaded and counted", () => {
  const p = base({ steam: { cs2_stats: { kd: 1.3, total_matches_played: 400 } } });
  assert.equal(stateOf(p, "Steam CS2"), "loaded");
  assert.equal(playerDataSources(p).cs2, true);
  assert.equal(playerHasAnyData(p), true);
});

test("built-in tabs are always the same five, in the historical order", () => {
  assert.deepEqual(tabsFor(base({ steam: {} })).map((t) => t.key), ["overview", "leetify", "cstracker", "steam", "ai"]);
  assert.deepEqual(tabsFor(base({ steam: {} })).map((t) => t.label), ["Огляд", "Leetify", "CSTracker", "Steam", "AI"]);
});

test("FACEIT appears in steam.sources but gets no chip and no tab (kept hidden)", () => {
  const p = base({ steam: { sources: { faceit: { status: "ok", data: { found: true, skill_level: 8 } } } } });
  assert.equal(sourceChipsFor(p).some((c) => c.label === "faceit" || c.label === "FACEIT"), false);
  assert.equal(sourceTabsFor(p).length, 2);  // leetify + cstracker only
});

test("unknown source 'newsrc': generic chip, tab, status mapping, flattened values, escaped", () => {
  const p = base({
    steam: {
      sources: {
        newsrc: {
          status: "ok",
          data: {
            note: "<img src=x onerror=alert(1)>",
            nested: { a: 1, b: "x" },
            arr: [1, "two"],
            long: "a".repeat(300),
          },
        },
      },
    },
  });
  const chip = sourceChipsFor(p).find((c) => c.label === "newsrc");
  assert.deepEqual(chip, { label: "newsrc", state: "loaded", retry: null });

  const tab = sourceTabsFor(p).find((t) => t.label === "newsrc");
  assert.equal(tab.key, "src-newsrc");
  const html = tab.render(p);
  assert.ok(!html.includes("<img"), "raw markup from data must not reach the DOM");
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"), "value is escaped");
  assert.ok(html.includes("nested.a") && html.includes("nested.b"), "one-level objects flatten to a.b rows");
  assert.ok(html.includes("1, two"), "arrays are joined");
  assert.ok(html.includes("…") && !html.includes("a".repeat(250)), "long strings are capped");
});

test("status mapping for generic sources", () => {
  const stateFor = (status) => sourceChipsFor(base({ steam: { sources: { x: { status, data: {} } } } }))[4].state;
  assert.equal(stateFor("ok"), "loaded");
  assert.equal(stateFor("not_found"), "none");
  assert.equal(stateFor("unavailable"), "none");
  assert.equal(stateFor("blocked"), "blocked");
  assert.equal(stateFor("rate_limited"), "rate");
  assert.equal(stateFor("error"), "error");
  assert.equal(stateFor("private"), "none");
  assert.equal(stateFor("something_new"), "none");
});

test("generic tab key and label are escaped/sanitized in the markup", () => {
  const p = base({ steam: { sources: { '<b>x</b>': { status: "ok", data: {} } } } });
  const strip = renderSourceStrip(p);
  assert.ok(strip.includes("&lt;b&gt;x&lt;/b&gt;"), "chip label escaped");
  assert.ok(!strip.includes("<b>x</b>"));
  assert.equal(sourceTabsFor(p).find((t) => t.label === "<b>x</b>").key, "src-_b_x__b_");
});

test("tab order: source tabs in place, generic tab after built-in source tabs and before Steam", () => {
  const p = base({ steam: { sources: { newsrc: { status: "ok", data: {} } } } });
  assert.deepEqual(tabsFor(p).map((t) => t.key), ["overview", "leetify", "cstracker", "src-newsrc", "steam", "ai"]);
});

test("uid is additive: markup with player_uid equals markup without it once the attribute is removed", () => {
  const legacy = base({ nick: "dup", steam: { leetify: { aim: 81 }, extra_stats: { cstracker: { status: "blocked" } } } });
  const withUid = { ...legacy, player_uid: "scan-1-0" };
  const stripUid = (html) => html.replace(/ data-player-uid="[^"]*"/g, "");
  assert.ok(renderPlayerDetailsRow(withUid).includes('data-player-uid="scan-1-0"'));
  assert.equal(renderPlayerDetailsRow(legacy).includes("data-player-uid"), false);
  assert.equal(stripUid(renderPlayerDetailsRow(withUid)), renderPlayerDetailsRow(legacy));
  assert.equal(stripUid(renderSourceStrip(withUid)), renderSourceStrip(legacy));
});

test("retry buttons carry the uid when the record has one", () => {
  const p = base({ nick: "dup", player_uid: "scan-2-1", steam: {} });
  const html = renderSourceStrip(p);
  assert.match(html, /source-retry-btn" data-nick="dup" data-player-uid="scan-2-1" data-source="leetify"/);
});

test("generic source label: raw id without metadata, registry label once state.sourceMeta is loaded", () => {
  const p = base({ steam: { sources: { example: { status: "ok", data: {} } } } });
  assert.equal(sourceChipsFor(p).find((c) => c.state === "loaded").label, "example");
  assert.equal(sourceTabsFor(p).find((t) => t.key === "src-example").label, "example");
  state.sourceMeta.set("example", { id: "example", label: "Example" });
  try {
    assert.equal(sourceChipsFor(p).find((c) => c.state === "loaded").label, "Example");
    assert.equal(sourceTabsFor(p).find((t) => t.key === "src-example").label, "Example");
  } finally {
    state.sourceMeta.delete("example");
  }
  assert.equal(sourceChipsFor(p).find((c) => c.state === "loaded").label, "example");
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL ${name}\n     ${err.message.split("\n").slice(0, 6).join("\n     ")}`);
  }
}
console.log(`${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
