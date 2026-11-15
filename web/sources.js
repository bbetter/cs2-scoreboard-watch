import { SOURCE_STATE_LABELS, state } from "/state.js";
import { detailKv, detailSection, detailSparkRow, escapeHtml, fmtNum, hasDataObject, uidAttr, withLobbyTag } from "/util.js";

// External data sources shown per player: chips, the lobby summary, and the per-player tabs.
//
// One entry per source. Built-in entries keep the exact chip states, tab bodies and lobby counts the UI had
// before the registry; an id in steam.sources that is not listed here gets a generic chip and tab (see
// genericSourceEntries). `hidden` sources (FACEIT) are listed only so that they never get a generic chip.

export const STATUS_TO_STATE = {
  ok: "loaded",
  not_found: "none",
  unavailable: "none",
  blocked: "blocked",
  rate_limited: "rate",
  error: "error",
  private: "none",
};

export const CSTRACKER_CHIP_STATE = { ok: "loaded", not_found: "none", blocked: "blocked", error: "error" };
export const GENERIC_CHARS_LIMIT = 200;

export function steamChipState(p) {
  const steam = p.steam || {};
  if (steam.lookup_source === "rate_limited") return "rate";
  if (steam.steamid64 || steam.profile_name) return "loaded";
  return "none";
}

export function cstrackerOf(p) {
  return ((p.steam || {}).extra_stats || {}).cstracker || {};
}

export const SOURCES = [
  {
    id: "steam",
    label: "Steam",
    chip: true,
    lobby: false,
    retryable: false,
    tab: null,
    state: steamChipState,
    hasData: () => false,
  },
  {
    id: "leetify",
    label: "Leetify",
    chip: true,
    lobby: true,
    countKey: "leetify",
    retryable: true,
    // A failed Leetify request looks like "none" in the record, so the chip has no error state.
    state: (p) => ((p.steam || {}).leetify ? "loaded" : "none"),
    hasData: (p) => hasDataObject((p.steam || {}).leetify),
    tab: {
      key: "leetify",
      label: "Leetify",
      render: (p) => {
        const steam = p.steam || {};
        return detailSection("Leetify", [...leetifyRows(steam.leetify || {}), leetifyAttribution()]);
      },
    },
  },
  {
    id: "cstracker",
    label: "CSTracker",
    chip: true,
    lobby: true,
    countKey: "cstracker",
    retryable: true,
    state: (p) => CSTRACKER_CHIP_STATE[cstrackerOf(p).status] || "none",
    hasData: (p) => cstrackerOf(p).status === "ok",
    tab: {
      key: "cstracker",
      label: "CSTracker",
      render: (p) => renderCstrackerSection(((p.steam || {}).extra_stats || {}).cstracker),
    },
  },
  {
    id: "steam_stats",
    label: "Steam CS2",
    chip: true,
    lobby: true,
    countKey: "cs2",
    retryable: false,
    tab: null,
    state: (p) => (hasDataObject((p.steam || {}).cs2_stats) ? "loaded" : "none"),
    hasData: (p) => hasDataObject((p.steam || {}).cs2_stats),
  },
  {
    id: "faceit",
    label: "FACEIT",
    chip: false,
    lobby: false,
    retryable: false,
    tab: null,
    hidden: true,
    state: () => "none",
    hasData: () => false,
  },
];

export const KNOWN_SOURCE_IDS = new Set(SOURCES.map((s) => s.id));
export const LOBBY_SOURCES = SOURCES.filter((s) => s.lobby);

// Entries of steam.sources (backend view) for ids this file does not know about.
export function genericSourceEntries(p) {
  const sources = (p.steam || {}).sources || {};
  return Object.entries(sources).filter(([id, entry]) => !KNOWN_SOURCE_IDS.has(id) && entry && typeof entry === "object");
}

// Human label from GET /api/sources (state.sourceMeta); the raw id until that metadata is loaded.
export function sourceLabelOf(id) {
  return state.sourceMeta.get(id)?.label ?? id;
}

export function genericTabKey(id) {
  return `src-${String(id).replace(/[^\w-]/g, "_")}`;
}

export function capText(text) {
  return text.length > GENERIC_CHARS_LIMIT ? `${text.slice(0, GENERIC_CHARS_LIMIT)}…` : text;
}

export function formatGenericValue(v) {
  if (v == null) return "—";
  if (Array.isArray(v)) {
    return capText(v.map((x) => (x && typeof x === "object" ? JSON.stringify(x) : String(x))).join(", "));
  }
  if (typeof v === "object") return capText(JSON.stringify(v));
  return capText(String(v));
}

// Objects one level deep become "a.b" rows; anything deeper is shown as JSON text.
export function genericRows(data) {
  const rows = [];
  for (const [k, v] of Object.entries(data || {})) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v)) rows.push([`${k}.${k2}`, formatGenericValue(v2)]);
    } else {
      rows.push([k, formatGenericValue(v)]);
    }
  }
  return rows;
}

export function genericTabBody(entry) {
  return detailSection(
    "Дані джерела",
    genericRows(entry.data).map(([label, value]) => detailKv(label, value)),
  );
}

// Count of players with data for each lobby source, keyed by the source's countKey (legacy names kept).
export function playerDataSources(p) {
  return Object.fromEntries(LOBBY_SOURCES.map((s) => [s.countKey, s.hasData(p)]));
}

export function playerHasAnyData(p) {
  return LOBBY_SOURCES.some((s) => s.hasData(p));
}

// One chip per visible source, in SOURCES order, then generic chips in steam.sources order.
export function sourceChipsFor(p) {
  const builtin = SOURCES.filter((s) => s.chip).map((s) => ({
    label: s.label,
    state: s.state(p),
    retry: s.retryable ? s.id : null,
  }));
  const generic = genericSourceEntries(p).map(([id, entry]) => ({
    label: sourceLabelOf(id),
    state: STATUS_TO_STATE[entry.status] || "none",
    retry: null,
  }));
  return [...builtin, ...generic];
}

// Source tabs in SOURCES order (all built-in tabs, as before), then generic tabs. The caller places the
// Overview / Steam / AI tabs around them.
export function sourceTabsFor(p) {
  const builtin = SOURCES.filter((s) => s.tab).map((s) => s.tab);
  const generic = genericSourceEntries(p).map(([id, entry]) => ({
    key: genericTabKey(id),
    label: sourceLabelOf(id),
    render: () => genericTabBody(entry),
  }));
  return [...builtin, ...generic];
}

export function renderSourceStrip(p) {
  const chips = sourceChipsFor(p)
    .map((c) => {
      const retry = c.retry
        ? `<button type="button" class="btn btn-secondary btn-sm source-retry-btn" data-nick="${escapeHtml(p.nick)}"${uidAttr(p)} data-source="${c.retry}">Повторити</button>`
        : "";
      return `<div class="source-chip source-${c.state}"><span class="source-chip-name">${escapeHtml(c.label)}</span><span class="source-chip-state">${SOURCE_STATE_LABELS[c.state]}</span>${retry}</div>`;
    })
    .join("");
  return `<div class="source-strip" role="group" aria-label="Статус джерел даних">${chips}</div>`;
}


export function leetifyAttribution() {
  return '<div class="leetify-attribution"><a href="https://leetify.com/" target="_blank" rel="noopener noreferrer">Data Provided by Leetify</a></div>';
}


export function leetifySparkRows(lf) {
  const pls = (lf.matches || [])
    .filter((m) => m && m.player)
    .sort((a, b) => String(a.finished_at || "").localeCompare(String(b.finished_at || "")))
    .map((m) => m.player);
  return [
    detailSparkRow("Leetify rating (історія)", pls.map((p) => p.leetify_rating), { fmt: (v) => v.toFixed(2) }),
    detailSparkRow("K/D (історія)", pls.map((p) => (p.total_deaths ? p.total_kills / p.total_deaths : NaN)), { fmt: (v) => v.toFixed(2) }),
  ];
}


export function cstrackerSparkRows(matches) {
  const chrono = [...(matches || [])].reverse();
  return [
    detailSparkRow("Rating (історія CSTracker)", chrono.map((m) => m.rating), { fmt: (v) => v.toFixed(2) }),
    detailSparkRow("TTD, ms (історія)", chrono.map((m) => m.ttd), { fmt: (v) => `${Math.round(v)}` }),
    detailSparkRow("ACC, % (історія)", chrono.map((m) => m.acc), { fmt: (v) => v.toFixed(1) }),
  ];
}


export function leetifyRows(lf) {
  if (!lf || !Object.keys(lf).length) {
    return [detailKv("Статус", "Немає профілю в Leetify (не зареєстрований або профіль прихований)")];
  }
  const r = lf.rating || {};
  const s = lf.stats || {};
  const rk = lf.ranks || {};
  const agg = lf.aggregate || {};
  const competitive = (rk.competitive || []).map((c) => `${String(c.map_name || "").replace(/^de_/, "")} ${c.rank}`).join(", ");
  const bans = (lf.bans || []).map((b) => b.platform).filter(Boolean).join(", ");
  const recent = (lf.matches || []).slice(0, 5).map((m) => {
    const pl = m.player || {};
    const teams = m.team_scores || [];
    const mine = teams.find((t) => t.team_number === pl.initial_team_number);
    const opp = teams.find((t) => t.team_number !== pl.initial_team_number);
    const res = mine && opp ? (mine.score > opp.score ? "W" : mine.score < opp.score ? "L" : "D") : "?";
    const date = String(m.finished_at || "").slice(0, 10);
    const map = String(m.map_name || "").replace(/^de_/, "");
    return detailKv(`${date} · ${map} · ${res}`, `${pl.total_kills ?? "—"}/${pl.total_deaths ?? "—"}/${pl.total_assists ?? "—"} · rating ${fmtNum(pl.leetify_rating, 2) ?? "—"}`);
  });
  const dash = (x) => x ?? "—";
  return [
    detailKv("Рейтинг Leetify", withLobbyTag(fmtNum(rk.leetify ?? lf.leetify_rating, 2), "lf_rating", rk.leetify ?? lf.leetify_rating)),
    detailKv("Aim / Positioning / Utility", [withLobbyTag(fmtNum(r.aim), "lf_aim", r.aim) ?? "—", fmtNum(r.positioning), fmtNum(r.utility)].map(dash).join(" / ")),
    detailKv("Clutch / Opening", [fmtNum(r.clutch, 2), fmtNum(r.opening, 2)].map(dash).join(" / ")),
    detailKv("CT / T Leetify", [fmtNum(r.ct_leetify, 2), fmtNum(r.t_leetify, 2)].map(dash).join(" / ")),
    detailKv("Матчі / Winrate", `${lf.total_matches ?? "—"} / ${fmtNum(lf.winrate, 1, "%") ?? "—"}`),
    detailKv("K/D / HS% (історія)", `${agg.kd != null ? agg.kd.toFixed(2) : "—"} / ${fmtNum(agg.hs_pct, 1, "%") ?? "—"}`),
    detailKv("Reaction time", withLobbyTag(s.reaction_time_ms != null ? `${Math.round(s.reaction_time_ms)} ms` : null, "reaction", s.reaction_time_ms)),
    detailKv("Preaim / Accuracy head", `${fmtNum(s.preaim, 1, "°") ?? "—"} / ${fmtNum(s.accuracy_head, 1, "%") ?? "—"}`),
    detailKv("Counter-strafe / Spray", `${fmtNum(s.counter_strafing_good_shots_ratio, 1, "%") ?? "—"} / ${fmtNum(s.spray_accuracy, 1, "%") ?? "—"}`),
    detailKv("Premier / FACEIT / Wingman", `${rk.premier ?? "—"} / ${rk.faceit ?? "—"} / ${rk.wingman ?? "—"}`),
    detailKv("Конкурентні ранги", competitive),
    detailKv("Бани платформ", bans || "немає"),
    detailKv("Приватність", lf.privacy_mode),
    detailKv("Перший матч", lf.first_match_date),
    ...leetifySparkRows(lf),
    recent.join(""),
  ];
}


export function renderCstrackerSection(ct) {
  if (!ct || !ct.status) {
    return detailSection("CSTracker", [detailKv("Статус", "Ще не запитувалось")]);
  }
  const statusText = {
    ok: "дані отримано",
    not_found: "профіль не знайдено",
    blocked: "сайт заблокував запит (Cloudflare або ліміт)",
    error: "помилка запиту",
  }[ct.status] || ct.status;
  if (ct.status !== "ok") {
    return detailSection("CSTracker", [detailKv("Статус", statusText)]);
  }
  const sec = ct.sections || {};
  const aim = sec.aim_reactions || {};
  const beh = sec.behavior || {};
  const gen = sec.general || {};
  const fmt = (v, suffix = "") => (v == null ? null : `${typeof v === "number" ? v.toFixed(1) : v}${suffix}`);
  const matches = ct.matches || [];
  const ttdVals = matches.map((m) => m.ttd).filter((v) => typeof v === "number");
  const avgTtd = ttdVals.length ? Math.round(ttdVals.reduce((a, b) => a + b, 0) / ttdVals.length) : null;
  const recent = matches.slice(0, 5).map((m) =>
    detailKv(`${String(m.when || "").replace(/^played\s*/, "")} · ${String(m.match || "").split(" ")[0]}`,
      `${m.kills ?? "—"}/${m.deaths ?? "—"}/${m.assists ?? "—"} · rating ${fmt(m.rating)} · ACC ${fmt(m.acc, "%")}`)
  ).join("");
  return detailSection("CSTracker", [
    detailKv("Trust rating", withLobbyTag(fmt(ct.trust, "%"), "ct_trust", ct.trust)),
    detailKv("Accuracy / Spray", `${fmt(aim.Accuracy, "%") ?? "—"} / ${fmt(aim["Spray accuracy"], "%") ?? "—"}`),
    detailKv("Preaim / Aim offset", `${withLobbyTag(fmt(aim.Preaim, "°"), "ct_preaim", aim.Preaim) ?? "—"} / ${fmt(aim["Aim offset"], "°") ?? "—"}`),
    detailKv("Spot to kill", aim["Spot to kill"] != null ? `${Math.round(aim["Spot to kill"])} ms` : null),
    detailKv("Counter-strafing", fmt(aim["Counter-strafing"], "%")),
    detailKv("TTD (середнє, матчі)", avgTtd != null ? `${avgTtd} ms` : null),
    detailKv("K / D / A (загалом) / Win rate", `${fmt(gen["K/D/A"]) ?? "—"} / ${fmt(gen["Win rate"], "%") ?? "—"}`),
    detailKv("Teamkills / AFK per match", `${fmt(beh["Teamkills / match"]) ?? "—"} / ${fmt(beh["AFK time / match"]) ?? "—"}`),
    ...cstrackerSparkRows(matches),
    recent,
  ]);
}



