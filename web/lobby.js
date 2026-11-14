import { LOBBY_SOURCES, playerHasAnyData } from "/sources.js";
import { LOBBY_PREFS_KEY, RISK_FILTER_VALUES, RISK_LABELS, els, state } from "/state.js";

export function playerIsHighRisk(p) {
  return p.risk_level === "high" || p.risk_level === "critical";
}

export function renderLobbySummary(players) {
  if (!els.lobbyRisk || !els.lobbySources) return;
  if (!players.length) {
    els.lobbyRisk.textContent = "Немає гравців у скані";
    els.lobbySources.textContent = "";
    return;
  }
  const risk = { low: 0, medium: 0, high: 0, critical: 0 };
  const counts = Object.fromEntries(LOBBY_SOURCES.map((s) => [s.id, 0]));
  let none = 0;
  for (const p of players) {
    const level = p.risk_level || "low";
    if (Object.prototype.hasOwnProperty.call(risk, level)) risk[level] += 1;
    for (const s of LOBBY_SOURCES) if (s.hasData(p)) counts[s.id] += 1;
    if (!playerHasAnyData(p)) none += 1;
  }
  const total = players.length;
  els.lobbyRisk.innerHTML =
    `<span class="lobby-src">Ризик:</span>` +
    Object.keys(RISK_LABELS)
      .map((k) => `<span class="risk-pill risk-${k}">${RISK_LABELS[k]}: ${risk[k]}</span>`)
      .join("");
  els.lobbySources.innerHTML =
    `<span class="lobby-src">Дані:</span>` +
    LOBBY_SOURCES.map((s) => `<span class="lobby-src">${s.label}: <strong>${counts[s.id]}/${total}</strong></span>`).join("") +
    `<span class="lobby-src">Без даних: <strong>${none}/${total}</strong></span>`;
}

export function loadLobbyPrefs() {
  try {
    const raw = localStorage.getItem(LOBBY_PREFS_KEY);
    if (!raw) return;
    const prefs = JSON.parse(raw);
    if (RISK_FILTER_VALUES.includes(prefs.riskFilter)) state.riskFilter = prefs.riskFilter;
    state.onlyWithData = prefs.onlyWithData === true;
    state.onlyHighRisk = prefs.onlyHighRisk === true;
  } catch (_err) {
    // Пошкоджені налаштування ігноруємо — лишаються значення за замовчуванням.
  }
  els.riskFilter.value = state.riskFilter;
  els.onlyWithDataFilter.checked = state.onlyWithData;
  els.onlyHighRiskFilter.checked = state.onlyHighRisk;
}

export function saveLobbyPrefs() {
  try {
    localStorage.setItem(
      LOBBY_PREFS_KEY,
      JSON.stringify({
        riskFilter: state.riskFilter,
        onlyWithData: state.onlyWithData,
        onlyHighRisk: state.onlyHighRisk,
      }),
    );
  } catch (_err) {
    // localStorage недоступний (приватний режим тощо) — фільтри працюють без збереження.
  }
}

export function applyPlayerFilters(players) {
  const q = state.searchQuery.trim().toLowerCase();
  return players.filter((p) => {
    const steam = p.steam || {};
    if (state.onlyWithData && !playerHasAnyData(p)) return false;
    if (state.onlyHighRisk && !playerIsHighRisk(p)) return false;
    if (state.riskFilter === "high-critical" && p.cheat_probability < 55) return false;
    if (state.riskFilter === "suspicious" && p.cheat_probability < 55) return false;
    if (state.riskFilter === "smurf" && (p.smurf_probability || 0) < 55) return false;
    if (state.riskFilter === "critical" && p.cheat_probability < 75) return false;
    if (state.riskFilter === "banned") {
      const hasBans = steam.vac_banned || (steam.leetify?.bans?.length > 0);
      if (!hasBans) return false;
    }
    if (q) {
      const hay = [
        p.nick,
        steam.profile_name,
        steam.steamid64,
        ...(p.reasons || []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export function sortPlayers(players) {
  const { sortKey, sortDir } = state;
  const mult = sortDir === "ascending" ? 1 : -1;
  return [...players].sort((a, b) => {
    const va = a[sortKey];
    const vb = b[sortKey];
    if (typeof va === "number" && typeof vb === "number") {
      return (va - vb) * mult;
    }
    return String(va ?? "").localeCompare(String(vb ?? ""), "uk") * mult;
  });
}

export function updateRateLimitBanner(players) {
  const banner = document.getElementById("rateLimitBanner");
  if (!banner) return;
  banner.hidden = !players.some((p) => p.steam?.lookup_source === "rate_limited");
}

export function players_ready_for_ai(p) {
  return Boolean(p && p.loading_stage !== "steam" && !p.loading);
}

