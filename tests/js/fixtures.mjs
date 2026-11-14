// Player fixtures for the frontend render snapshot. Fake data only (no real keys, no real profiles).
const SID = "76561198000000001";

const base = (over = {}) => ({
  nick: "player", kills: 16, deaths: 18, kd: 0.89, hs: 42, level: 18, score: 28,
  cheat_probability: 20, smurf_probability: 5, risk_level: "low", smurf_risk_level: "low",
  uncertainty: 40, effective_weights: { scoreboard: 0.5, leetify: 0, steam_account: 0.5 },
  reasons: [], anomaly_score: 28, loading: false, loading_stage: "done",
  steam: { steamid64: SID, privacy_state: "public", cs2_hours: 900, account_age_years: 6 },
  ...over,
});

const leetify = {
  total_matches: 120, winrate: 58.5, aim: 81.2, reaction_time_ms: 340, preaim: 6.2,
  accuracy_head: 44.1, kd: 1.2, hs_pct: 46, leetify_rating: 0.11, premier: 15000, faceit: 6,
  bans: [], stats: { counter_strafing_good_shots_ratio: 92.5, spray_accuracy: 47.0 },
};
const ct = (status, extra = {}) => ({ status, trust: 80, matches: [], sections: { aim_reactions: { "Spot to kill": 290 } }, ...extra });

export const FIXTURES = {
  empty: base({ nick: "empty", steam: {} }),
  leetify_only: base({ nick: "lf", steam: { ...base().steam, leetify } }),
  leetify_empty_object: base({ nick: "lf_empty", steam: { ...base().steam, leetify: {} } }),
  cstracker_ok: base({ nick: "ct_ok", steam: { ...base().steam, extra_stats: { cstracker: ct("ok"), _cs2_checked: true } } }),
  cstracker_blocked: base({ nick: "ct_blocked", steam: { ...base().steam, extra_stats: { cstracker: { status: "blocked", http_status: 403 } } } }),
  cstracker_not_found: base({ nick: "ct_nf", steam: { ...base().steam, extra_stats: { cstracker: { status: "not_found" } } } }),
  cstracker_error: base({ nick: "ct_err", steam: { ...base().steam, extra_stats: { cstracker: { status: "error" } } } }),
  steam_rate_limited: base({ nick: "rate", steam: { lookup_source: "rate_limited" } }),
  cs2_stats_only: base({ nick: "cs2", steam: { ...base().steam, cs2_stats: { kd: 1.3, total_matches_played: 400, hs_pct: 50 } } }),
  loading_row: base({ nick: "loading", loading: true, loading_stage: "steam", steam: {} }),
  full_mix: base({
    nick: "full", risk_level: "critical", cheat_probability: 84, smurf_probability: 40,
    reasons: ["VAC-бан у Steam", "Надлюдська реакція Leetify (340 мс)"],
    steam: {
      ...base().steam, vac_banned: true, leetify, cs2_stats: { kd: 2.1, total_matches_played: 90, hs_pct: 71 },
      extra_stats: { cstracker: ct("ok"), _cs2_checked: true },
    },
  }),
};

// Lobby-level fixture: several players with mixed states, used for the lobby summary counts.
export const LOBBY = [FIXTURES.empty, FIXTURES.leetify_only, FIXTURES.leetify_empty_object, FIXTURES.cstracker_ok,
  FIXTURES.cstracker_blocked, FIXTURES.steam_rate_limited, FIXTURES.cs2_stats_only, FIXTURES.full_mix];
