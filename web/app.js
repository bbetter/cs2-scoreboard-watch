const state = {
  currentScan: null,
  matches: [],
  history: [],
  sortKey: "cheat_probability",
  sortDir: "descending",
  searchQuery: "",
  dateFilter: "",
  matchFilter: "",
  riskFilter: "all",
  onlyWithData: false,
  onlyHighRisk: false,
  expandedNicks: new Set(),
};

const els = {
  sseStatus: document.getElementById("sseStatus"),
  sseLabel: document.getElementById("sseLabel"),
  scanNowBtn: document.getElementById("scanNowBtn"),
  rescanBtn: document.getElementById("rescanBtn"),
  fileInput: document.getElementById("fileInput"),
  dropZone: document.getElementById("dropZone"),
  searchInput: document.getElementById("searchInput"),
  dateFilter: document.getElementById("dateFilter"),
  matchSelect: document.getElementById("matchSelect"),
  riskFilter: document.getElementById("riskFilter"),
  lobbyRisk: document.getElementById("lobbyRisk"),
  lobbySources: document.getElementById("lobbySources"),
  onlyWithDataFilter: document.getElementById("onlyWithDataFilter"),
  onlyHighRiskFilter: document.getElementById("onlyHighRiskFilter"),
  clearFiltersBtn: document.getElementById("clearFiltersBtn"),
  setupCs2Btn: document.getElementById("setupCs2Btn"),
  aiAnalyzeScanBtn: document.getElementById("aiAnalyzeScanBtn"),
  toolsMenu: document.getElementById("toolsMenu"),
  settingsBtn: document.getElementById("settingsBtn"),
  helpBtn: document.getElementById("helpBtn"),
  cs2SetupDialog: document.getElementById("cs2SetupDialog"),
  settingsDialog: document.getElementById("settingsDialog"),
  settingsForm: document.getElementById("settingsForm"),
  closeSettingsXBtn: document.getElementById("closeSettingsXBtn"),
  cancelSettingsBtn: document.getElementById("cancelSettingsBtn"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
  settingsSaveMsg: document.getElementById("settingsSaveMsg"),
  cfgTabDelay: document.getElementById("cfgTabDelay"),
  cfgTabCooldown: document.getElementById("cfgTabCooldown"),
  cfgCs2Only: document.getElementById("cfgCs2Only"),
  cfgNotifyEnabled: document.getElementById("cfgNotifyEnabled"),
  cfgCs2Dir: document.getElementById("cfgCs2Dir"),
  cfgCs2DirHint: document.getElementById("cfgCs2DirHint"),
  cfgConsoleLog: document.getElementById("cfgConsoleLog"),
  cfgConsoleLogHint: document.getElementById("cfgConsoleLogHint"),
  cfgScreenshotsDir: document.getElementById("cfgScreenshotsDir"),
  cfgScreenshotsDirHint: document.getElementById("cfgScreenshotsDirHint"),
  resetPathsBtn: document.getElementById("resetPathsBtn"),
  cfgScreenshotTool: document.getElementById("cfgScreenshotTool"),
  cfgSteamApiKey: document.getElementById("cfgSteamApiKey"),
  cfgSteamKeyStatus: document.getElementById("cfgSteamKeyStatus"),
  clearSteamKeyBtn: document.getElementById("clearSteamKeyBtn"),
  cfgFaceitApiKey: document.getElementById("cfgFaceitApiKey"),
  cfgFaceitKeyStatus: document.getElementById("cfgFaceitKeyStatus"),
  clearFaceitKeyBtn: document.getElementById("clearFaceitKeyBtn"),
  cfgAiEnabled: document.getElementById("cfgAiEnabled"),
  cfgAiProvider: document.getElementById("cfgAiProvider"),
  cfgAiModel: document.getElementById("cfgAiModel"),
  cfgWeightScoreboard: document.getElementById("cfgWeightScoreboard"),
  cfgWeightLeetify: document.getElementById("cfgWeightLeetify"),
  cfgWeightSteam: document.getElementById("cfgWeightSteam"),
  cfgAiPrompt: document.getElementById("cfgAiPrompt"),
  cfgAnthropicApiKey: document.getElementById("cfgAnthropicApiKey"),
  cfgAnthropicKeyStatus: document.getElementById("cfgAnthropicKeyStatus"),
  clearAnthropicKeyBtn: document.getElementById("clearAnthropicKeyBtn"),
  cfgClaudeCliStatus: document.getElementById("cfgClaudeCliStatus"),
  helpDialog: document.getElementById("helpDialog"),
  closeHelpBtn: document.getElementById("closeHelpBtn"),
  closeHelpXBtn: document.getElementById("closeHelpXBtn"),
  setupStatusText: document.getElementById("setupStatusText"),
  launchOptionsCode: document.getElementById("launchOptionsCode"),
  copyLaunchBtn: document.getElementById("copyLaunchBtn"),
  closeSetupBtn: document.getElementById("closeSetupBtn"),
  historySelect: document.getElementById("historySelect"),
  statusMsg: document.getElementById("statusMsg"),
  statPlayers: document.getElementById("statPlayers"),
  statSuspects: document.getElementById("statSuspects"),
  statTopSuspect: document.getElementById("statTopSuspect"),
  statLastScan: document.getElementById("statLastScan"),
  playersTable: document.getElementById("playersTable"),
  playersBody: document.getElementById("playersBody"),
  previewDetails: document.getElementById("previewDetails"),
  screenshotPreview: document.getElementById("screenshotPreview"),
  tabPanelTable: document.getElementById("tabPanelTable"),
  tabPanelLogs: document.getElementById("tabPanelLogs"),
  tabCountTable: document.getElementById("tabCountTable"),
  tabCountLogs: document.getElementById("tabCountLogs"),
  logsContainer: document.getElementById("logsContainer"),
};

const RISK_LABELS = {
  low: "Низький",
  medium: "Середній",
  high: "Високий",
  critical: "Критичний",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setStatus(text, isError = false) {
  els.statusMsg.textContent = text;
  els.statusMsg.style.color = isError ? "var(--critical)" : "var(--accent)";
}

function formatDuration(sec) {
  if (!sec || sec < 60) return `${sec || 0} с`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m} хв ${s} с` : `${m} хв`;
}

const LOBBY_PREFS_KEY = "cswatcher.lobbyFilters.v1";
const RISK_FILTER_VALUES = ["all", "suspicious", "smurf", "critical", "banned"];

function hasDataObject(value) {
  return !!value && typeof value === "object" && Object.keys(value).length > 0;
}

// Which data sources a player record actually carries (see steam_lookup.py / web/index.html).
function playerDataSources(p) {
  const steam = p.steam || {};
  return {
    leetify: hasDataObject(steam.leetify),
    cstracker: steam.extra_stats?.cstracker?.status === "ok",
    cs2: hasDataObject(steam.cs2_stats),
  };
}

function playerHasAnyData(p) {
  const s = playerDataSources(p);
  return s.leetify || s.cstracker || s.cs2;
}

function playerIsHighRisk(p) {
  return p.risk_level === "high" || p.risk_level === "critical";
}

function renderLobbySummary(players) {
  if (!els.lobbyRisk || !els.lobbySources) return;
  if (!players.length) {
    els.lobbyRisk.textContent = "Немає гравців у скані";
    els.lobbySources.textContent = "";
    return;
  }
  const risk = { low: 0, medium: 0, high: 0, critical: 0 };
  const src = { leetify: 0, cstracker: 0, cs2: 0, none: 0 };
  for (const p of players) {
    const level = p.risk_level || "low";
    if (Object.prototype.hasOwnProperty.call(risk, level)) risk[level] += 1;
    const s = playerDataSources(p);
    if (s.leetify) src.leetify += 1;
    if (s.cstracker) src.cstracker += 1;
    if (s.cs2) src.cs2 += 1;
    if (!playerHasAnyData(p)) src.none += 1;
  }
  const total = players.length;
  els.lobbyRisk.innerHTML =
    `<span class="lobby-src">Ризик:</span>` +
    Object.keys(RISK_LABELS)
      .map((k) => `<span class="risk-pill risk-${k}">${RISK_LABELS[k]}: ${risk[k]}</span>`)
      .join("");
  els.lobbySources.innerHTML =
    `<span class="lobby-src">Дані:</span>` +
    [
      ["Leetify", src.leetify],
      ["CSTracker", src.cstracker],
      ["Steam CS2", src.cs2],
    ]
      .map(([label, n]) => `<span class="lobby-src">${label}: <strong>${n}/${total}</strong></span>`)
      .join("") +
    `<span class="lobby-src">Без даних: <strong>${src.none}/${total}</strong></span>`;
}

function loadLobbyPrefs() {
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

function saveLobbyPrefs() {
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

function applyPlayerFilters(players) {
  const q = state.searchQuery.trim().toLowerCase();
  return players.filter((p) => {
    const steam = p.steam || {};
    if (state.onlyWithData && !playerHasAnyData(p)) return false;
    if (state.onlyHighRisk && !playerIsHighRisk(p)) return false;
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

function sortPlayers(players) {
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

function renderScan(scan) {
  state.currentScan = scan;
  if (els.rescanBtn) {
    els.rescanBtn.disabled = !scan;
  }
  if (!scan) {
    return;
  }

  const players = scan.players || [];
  renderLobbySummary(players);
  const suspects = players.filter((p) => p.cheat_probability >= 55);
  const top = [...players].sort((a, b) => b.cheat_probability - a.cheat_probability)[0];

  els.statPlayers.textContent = String(players.length);
  els.statSuspects.textContent = String(suspects.length);
  els.statTopSuspect.textContent = top ? `${top.nick} (${top.cheat_probability}%)` : "—";
  els.statLastScan.textContent = `${scan.created_at.replace("T", " ")} (${scan.duration_ms} мс)`;

  if (scan.image_url) {
    els.previewDetails.hidden = false;
    if (els.screenshotPreview.dataset.scanImage !== scan.image_url) {
      els.screenshotPreview.dataset.scanImage = scan.image_url;
      els.screenshotPreview.src = `${scan.image_url}?t=${Date.now()}`;
    }
  } else {
    els.previewDetails.hidden = true;
    els.screenshotPreview.dataset.scanImage = "";
  }

  if (!players.length) {
    if (els.tabCountTable) els.tabCountTable.textContent = "0";
    if (els.tabCountLogs) els.tabCountLogs.textContent = "0";
    els.playersBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="8">Гравців не знайдено на скріншоті. Переконайтеся, що відкрита таблиця TAB.</td>
      </tr>`;
    renderLogs(scan);
    return;
  }

  renderLogs(scan);

  const sorted = sortPlayers(applyPlayerFilters(players));
  if (els.tabCountTable) {
    els.tabCountTable.textContent =
      sorted.length === players.length ? String(players.length) : `${sorted.length}/${players.length}`;
  }
  if (els.tabCountLogs) {
    els.tabCountLogs.textContent = String(players.length);
  }
  if (!sorted.length) {
    els.playersBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="8">За поточними фільтрами гравців не знайдено.</td>
      </tr>`;
    return;
  }
  els.playersBody.innerHTML = sorted
    .map((p) => {
      const steam = p.steam || {};
      const avSrc = steam.avatar_url || steam.screenshot_avatar;
      const avatarHtml = avSrc
        ? `<img class="avatar" src="${escapeHtml(avSrc)}" alt="" loading="lazy" />`
        : `<div class="avatar avatar-placeholder" aria-hidden="true">${escapeHtml(p.nick.slice(0, 1).toUpperCase())}</div>`;

      const isLoading = Boolean(p.loading);
      const profileName = isLoading
        ? '<span class="loading-inline"><span class="spinner-dot" aria-hidden="true"></span><span>Завантаження Steam / Leetify...</span></span>'
        : steam.profile_name
          ? `Steam: ${escapeHtml(steam.profile_name)}`
          : "Steam профіль не знайдено автоматично";

      const tags = [];
      if (steam.in_cs2) tags.push('<span class="tag tag-cs2">In CS2</span>');
      else if (steam.online_state === "online") tags.push('<span class="tag tag-online">Online</span>');
      if (steam.vac_banned) tags.push('<span class="tag tag-vac">VAC BAN</span>');
      if (steam.is_limited) tags.push('<span class="tag tag-limited">Limited</span>');
      if (steam.account_age_years != null) tags.push(`<span class="tag tag-meta">${steam.account_age_years}y</span>`);
      if (steam.cs2_hours != null) tags.push(`<span class="tag tag-meta">${Math.round(steam.cs2_hours)}h</span>`);
      if (steam.privacy_state && steam.privacy_state !== "public") {
        tags.push(`<span class="tag tag-private">${escapeHtml(steam.privacy_state)}</span>`);
      }
      const extra = steam.extra_stats || {};
      const ct = extra.cstracker || {};
      if (ct.vaclive > 0) tags.push(`<span class="tag tag-vac">VACLive ${ct.vaclive}</span>`);
      if (ct.trust != null) tags.push(`<span class="tag tag-meta">Trust ${Math.round(ct.trust)}%</span>`);
      if (extra.has_veteran_coin) tags.push('<span class="tag tag-meta">Veteran</span>');

      const lf = steam.leetify;

      const fc = steam.faceit;
      let faceitLine = "";
      if (fc?.found || fc?.skill_level != null) {
        const lvlText = fc.skill_level != null ? `FACEIT Lvl ${fc.skill_level}` : "FACEIT";
        const eloText = fc.faceit_elo != null ? ` (${fc.faceit_elo} ELO)` : "";
        faceitLine = `<span class="steam-sub">${escapeHtml(lvlText + eloText)}</span>`;
      }

      const cheatProb = Math.round(p.cheat_probability ?? 0);
      const smurfProb = Math.round(p.smurf_probability ?? 0);
      const cheatRiskLevel = p.risk_level || "low";
      const smurfRiskLevel = p.smurf_risk_level || "low";
      const riskLabel = RISK_LABELS[cheatRiskLevel] || cheatRiskLevel;
      const smurfRiskLabel = RISK_LABELS[smurfRiskLevel] || smurfRiskLevel;
      const uncertaintyNote = p.uncertainty != null
        ? `<div class="uncertainty-note">Невизначеність даних: ${Math.round(p.uncertainty)}%</div>`
        : "";
      const reasons = (p.reasons || []).slice(0, 3).map(escapeHtml).join(" · ");
      const ripUrl = steam.rip_url || `https://steamcommunity.rip/id/${encodeURIComponent(p.nick)}/`;
      const steamLink = steam.profile_url
        ? `<a class="link-btn" href="${escapeHtml(steam.profile_url)}" target="_blank" rel="noopener noreferrer">Steam</a>`
        : "";
      const leetifyLink = lf?.url
        ? `<a class="link-btn" href="${escapeHtml(lf.url)}" target="_blank" rel="noopener noreferrer">Leetify</a>`
        : "";
      const faceitLink = fc?.faceit_url
        ? `<a class="link-btn link-btn-faceit" href="${escapeHtml(fc.faceit_url)}" target="_blank" rel="noopener noreferrer">FACEIT</a>`
        : "";

      const ai = p.ai_analysis || null;
      const aiClass = ai ? `ai-${String(ai.decision || "uncertain").toLowerCase()}` : "";
      const aiRunBtn = (label, cls) =>
        `<button type="button" class="btn ${cls} btn-sm run-ai-player-btn" data-nick="${escapeHtml(p.nick)}">${label}</button>`;
      const aiBadge = ai
        ? `<div class="ai-row-actions"><span class="ai-decision-badge ${escapeHtml(aiClass)}">AI-DECISION: ${escapeHtml(ai.decision)} (${escapeHtml(ai.confidence)}%)</span>${aiRunBtn("↻ Перезапустити AI", "btn-secondary")}</div>`
        : p.loading_stage === "ai"
          ? `<div><span class="ai-loading-pill"><span class="spinner-dot" aria-hidden="true"></span>AI аналізує...</span></div>`
          : isLoading || !players_ready_for_ai(p)
            ? ""
            : `<div class="ai-row-actions">${aiRunBtn("Запустити AI-аналіз", "btn-primary")}</div>`;
      const isExpanded = state.expandedNicks.has(p.nick);

      const mainRow = `
        <tr class="player-row ${isExpanded ? "expanded" : ""}" data-nick="${escapeHtml(p.nick)}">
          <td data-label="Гравець">
            <div class="player-cell">
              <span class="expand-caret" aria-hidden="true">▸</span>
              ${avatarHtml}
              <div class="player-names">
                <div class="nick-row">
                  <span class="nick-main">${escapeHtml(p.nick)}</span>
                  <button type="button" class="edit-nick-btn" data-nick="${escapeHtml(p.nick)}" title="Виправити нік або вставити Steam URL / SteamID64">✎</button>
                </div>
                <span class="steam-sub">${profileName}</span>
                ${isLoading ? '<div class="skeleton-line" aria-hidden="true"></div>' : ""}
                ${!isLoading && faceitLine ? faceitLine : ""}
                ${!isLoading && tags.length ? `<div class="badge-row">${tags.join("")}</div>` : ""}
              </div>
            </div>
          </td>
          <td data-label="K">${p.kills}</td>
          <td data-label="D">${p.deaths}</td>
          <td data-label="K/D">${Number(p.kd).toFixed(2)}</td>
          <td data-label="HS%">${Number(p.hs).toFixed(0)}%</td>
          <td data-label="Lvl">${p.level >= 0 ? p.level : "?"}</td>
          <td data-label="Результати аналізу" class="prob-cell">
            <div class="dual-prob-list">
              <div class="dual-prob-item">
                <div class="prob-top">
                  <span class="dual-prob-label">Ймовірний чітер</span>
                  <span class="prob-num">${cheatProb}%</span>
                  <span class="risk-pill risk-${escapeHtml(cheatRiskLevel)}">${escapeHtml(riskLabel)}</span>
                </div>
                <div class="prob-bar" aria-hidden="true">
                  <div class="prob-fill fill-${escapeHtml(cheatRiskLevel)}" style="width: ${Math.min(100, Math.max(0, cheatProb))}%"></div>
                </div>
              </div>
              <div class="dual-prob-item">
                <div class="prob-top">
                  <span class="dual-prob-label">Ймовірний смурф</span>
                  <span class="prob-num">${smurfProb}%</span>
                  <span class="risk-pill risk-${escapeHtml(smurfRiskLevel)}">${escapeHtml(smurfRiskLabel)}</span>
                </div>
                <div class="prob-bar" aria-hidden="true">
                  <div class="prob-fill fill-smurf" style="width: ${Math.min(100, Math.max(0, smurfProb))}%"></div>
                </div>
              </div>
            </div>
            ${uncertaintyNote}
            <div class="reasons-text">${reasons}</div>
            ${aiBadge}
          </td>
          <td data-label="Additional Data">
            <div class="links-cell">
              ${leetifyLink}
              ${faceitLink}
              <a class="link-btn link-btn-rip" href="${escapeHtml(ripUrl)}" target="_blank" rel="noopener noreferrer">rip</a>
              ${steamLink}
            </div>
          </td>
        </tr>`;

      if (!isExpanded) return mainRow;
      return mainRow + renderPlayerDetailsRow(p);
    })
    .join("");
}

function detailKv(label, value) {
  if (value == null || value === "" || value === "—") return "";
  return `<div class="detail-kv"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function detailSection(title, rows) {
  const body = rows.filter(Boolean).join("");
  if (!body) return "";
  return `<div class="detail-section"><h5>${escapeHtml(title)}</h5><div class="detail-kv-list">${body}</div></div>`;
}

function fmtNum(v, d = 1, suffix = "") {
  return v == null || Number.isNaN(Number(v)) ? null : `${Number(v).toFixed(d)}${suffix}`;
}

function leetifyRows(lf) {
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
    detailKv("Рейтинг Leetify", fmtNum(rk.leetify ?? lf.leetify_rating, 2)),
    detailKv("Aim / Positioning / Utility", [fmtNum(r.aim), fmtNum(r.positioning), fmtNum(r.utility)].map(dash).join(" / ")),
    detailKv("Clutch / Opening", [fmtNum(r.clutch, 2), fmtNum(r.opening, 2)].map(dash).join(" / ")),
    detailKv("CT / T Leetify", [fmtNum(r.ct_leetify, 2), fmtNum(r.t_leetify, 2)].map(dash).join(" / ")),
    detailKv("Матчі / Winrate", `${lf.total_matches ?? "—"} / ${fmtNum(lf.winrate, 1, "%") ?? "—"}`),
    detailKv("K/D / HS% (історія)", `${agg.kd != null ? agg.kd.toFixed(2) : "—"} / ${fmtNum(agg.hs_pct, 1, "%") ?? "—"}`),
    detailKv("Reaction time", s.reaction_time_ms != null ? `${Math.round(s.reaction_time_ms)} ms` : null),
    detailKv("Preaim / Accuracy head", `${fmtNum(s.preaim, 1, "°") ?? "—"} / ${fmtNum(s.accuracy_head, 1, "%") ?? "—"}`),
    detailKv("Counter-strafe / Spray", `${fmtNum(s.counter_strafing_good_shots_ratio, 1, "%") ?? "—"} / ${fmtNum(s.spray_accuracy, 1, "%") ?? "—"}`),
    detailKv("Premier / FACEIT / Wingman", `${rk.premier ?? "—"} / ${rk.faceit ?? "—"} / ${rk.wingman ?? "—"}`),
    detailKv("Конкурентні ранги", competitive),
    detailKv("Бани платформ", bans || "немає"),
    detailKv("Приватність", lf.privacy_mode),
    detailKv("Перший матч", lf.first_match_date),
    recent.join(""),
  ];
}

function renderCstrackerSection(ct) {
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
    detailKv("Trust rating", fmt(ct.trust, "%")),
    detailKv("Accuracy / Spray", `${fmt(aim.Accuracy, "%") ?? "—"} / ${fmt(aim["Spray accuracy"], "%") ?? "—"}`),
    detailKv("Preaim / Aim offset", `${fmt(aim.Preaim, "°") ?? "—"} / ${fmt(aim["Aim offset"], "°") ?? "—"}`),
    detailKv("Spot to kill", aim["Spot to kill"] != null ? `${Math.round(aim["Spot to kill"])} ms` : null),
    detailKv("Counter-strafing", fmt(aim["Counter-strafing"], "%")),
    detailKv("TTD (середнє, матчі)", avgTtd != null ? `${avgTtd} ms` : null),
    detailKv("K / D / A (загалом) / Win rate", `${fmt(gen["K/D/A"]) ?? "—"} / ${fmt(gen["Win rate"], "%") ?? "—"}`),
    detailKv("Teamkills / AFK per match", `${fmt(beh["Teamkills / match"]) ?? "—"} / ${fmt(beh["AFK time / match"]) ?? "—"}`),
    recent,
  ]);
}

function players_ready_for_ai(p) {
  return Boolean(p && p.loading_stage !== "steam" && !p.loading);
}

function renderPlayerDetailsRow(p) {
  const steam = p.steam || {};
  const lf = steam.leetify || {};
  const fc = steam.faceit || {};
  const cs2 = steam.cs2_stats || {};
  const ew = p.effective_weights || {};
  const ai = p.ai_analysis || null;
  const allReasons = (p.reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("");
  const extraMeta = steam.extra_stats || {};

  const weightsText =
    ew.scoreboard != null || ew.leetify != null || ew.steam_account != null
      ? `Рахунок: ${Math.round((ew.scoreboard ?? 0) * 100)}%, Leetify/FACEIT: ${Math.round((ew.leetify ?? 0) * 100)}%, Steam: ${Math.round((ew.steam_account ?? 0) * 100)}%`
      : "—";

  const aiProbsNote =
    ai && (ai.cheat_probability != null || ai.smurf_probability != null)
      ? `<div class="ai-meta-note">Чітер: ${escapeHtml(ai.cheat_probability ?? "—")}% · Смурф: ${escapeHtml(ai.smurf_probability ?? "—")}%</div>`
      : "";

  const aiCardBody = ai
    ? `
      <div class="detail-card-head">
        <h4>AI-аналіз</h4>
        <span class="ai-decision-badge ai-${escapeHtml(String(ai.decision || "uncertain").toLowerCase())}">
          AI-DECISION: ${escapeHtml(ai.decision)} (${escapeHtml(ai.confidence)}%)
        </span>
      </div>
      <div class="ai-meta-note">Провайдер: ${escapeHtml(ai.provider || "claude_cli")} · Модель: ${escapeHtml(ai.model || "sonnet")} · ${escapeHtml((ai.analyzed_at || "").replace("T", " "))}</div>
      ${aiProbsNote}
      <div>
        <div class="ai-meta-note" style="margin-bottom:4px;">Chain of Thought:</div>
        <div class="ai-cot-block">${escapeHtml(ai.chain_of_thought || "—")}</div>
      </div>
      <div>
        <div class="ai-meta-note" style="margin-bottom:4px;">Висновок:</div>
        <div class="ai-summary-block">${escapeHtml(ai.summary || "—")}</div>
      </div>
      <div>
        <button type="button" class="btn btn-secondary btn-sm run-ai-player-btn" data-nick="${escapeHtml(p.nick)}">Перезапустити AI-аналіз</button>
      </div>`
    : `
      <div class="detail-card-head">
        <h4>AI-аналіз</h4>
      </div>
      <p class="ai-meta-note">AI-аналіз ще не виконувався для цього гравця (можна увімкнути автозапуск у Налаштуваннях або запустити вручну).</p>
      <div>
        <button type="button" class="btn btn-primary btn-sm run-ai-player-btn" data-nick="${escapeHtml(p.nick)}">Запустити AI-аналіз (Claude)</button>
      </div>`;

  return `
    <tr class="player-details-row">
      <td colspan="8">
        <div class="player-details-box">
          <div class="player-details-grid">
            <div class="detail-card">
              <div class="detail-card-head"><h4>Детальний аналіз</h4></div>
              ${detailSection("Оцінка", [
                detailKv("Ймовірність чітера / смурфа", `${Math.round(p.cheat_probability ?? 0)}% / ${Math.round(p.smurf_probability ?? 0)}%`),
                detailKv("Невизначеність", `${Math.round(p.uncertainty ?? 50)}%`),
                detailKv("Ваги джерел", weightsText),
              ])}
              ${detailSection("Steam", [
                detailKv("SteamID64", steam.steamid64),
                detailKv("Вік акаунта", steam.account_age_years != null ? `${steam.account_age_years} р.` : null),
                detailKv("Годин у CS2", steam.cs2_hours != null ? `${steam.cs2_hours} год` : null),
                detailKv("Рівень Steam", steam.steam_level),
                detailKv("VAC / Game бани", `${steam.number_of_vac_bans || 0} VAC / ${steam.game_bans || 0} Game`),
                detailKv("CS2 K/D / HS%", cs2.kd != null ? `${Number(cs2.kd).toFixed(2)} / ${fmtNum(cs2.hs_pct, 1, "%")}` : null),
                detailKv("CS2 вбивства / матчі", cs2.total_kills != null ? `${cs2.total_kills} / ${cs2.total_matches_played ?? "—"}` : null),
                detailKv("Метод пошуку", steam.lookup_method),
              ])}
              ${detailSection("Leetify", leetifyRows(lf))}
              ${detailSection("FACEIT", (fc.found || fc.skill_level != null) ? [
                detailKv("Рівень / ELO", `${fc.skill_level ?? "—"} / ${fc.faceit_elo ?? "—"}`),
                detailKv("K/D / WR / Матчі", `${fc.kd != null ? Number(fc.kd).toFixed(2) : "—"} / ${fmtNum(fc.winrate, 1, "%") ?? "—"} / ${fc.matches ?? "—"}`),
              ] : [])}
              ${renderCstrackerSection(extraMeta.cstracker)}
              ${allReasons ? `<ul class="detail-reasons">${allReasons}</ul>` : ""}
            </div>
            <div class="detail-card">
              ${aiCardBody}
            </div>
          </div>
        </div>
      </td>
    </tr>`;
}

function updateMatchAndHistorySelects() {
  const filteredMatches = state.matches.filter(
    (m) => !state.dateFilter || m.date === state.dateFilter
  );

  if (!filteredMatches.length) {
    els.matchSelect.innerHTML = '<option value="">Немає матчів</option>';
    els.matchSelect.disabled = true;
  } else {
    els.matchSelect.disabled = false;
    els.matchSelect.innerHTML =
      '<option value="">Усі матчі</option>' +
      filteredMatches
        .map((m) => {
          const mapPart = m.map_name ? `${m.map_name} · ` : "";
          const durPart = formatDuration(m.duration_sec);
          const label = `${m.date} · ${mapPart}${m.match_id.slice(0, 18)} (${durPart}, ${m.scan_count} скр., макс ${m.max_risk}%)`;
          return `<option value="${escapeHtml(m.match_id)}" ${
            m.match_id === state.matchFilter ? "selected" : ""
          }>${escapeHtml(label)}</option>`;
        })
        .join("");
  }

  const filteredScans = state.history.filter((item) => {
    if (state.dateFilter && item.date !== state.dateFilter) return false;
    if (state.matchFilter && item.match_id !== state.matchFilter) return false;
    return true;
  });

  if (!filteredScans.length) {
    els.historySelect.innerHTML = '<option value="">Немає сканів</option>';
    els.historySelect.disabled = true;
    return;
  }
  const activeId = state.currentScan?.id || "";
  els.historySelect.disabled = false;
  els.historySelect.innerHTML = filteredScans
    .map(
      (item) =>
        `<option value="${escapeHtml(item.id)}" ${item.id === activeId ? "selected" : ""}>${escapeHtml(
          item.created_at.replace("T", " ")
        )} — ${item.player_count} гравців</option>`
    )
    .join("");
}

async function fetchLatest() {
  try {
    const res = await fetch("/api/latest");
    const data = await res.json();
    state.matches = data.matches || [];
    state.history = data.history || [];
    if (data.latest && !state.currentScan) {
      renderScan(data.latest);
    } else if (data.latest && state.currentScan?.id === data.latest.id) {
      renderScan(data.latest);
    }
    updateMatchAndHistorySelects();
  } catch (err) {
    setStatus(`Помилка завантаження: ${err.message}`, true);
  }
}

async function triggerScan() {
  els.scanNowBtn.disabled = true;
  setStatus("Роблю скріншот і сканую таблицю...");
  try {
    const res = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "Scan failed");
    }
    renderScan(data);
    await fetchLatest();
    setStatus(`Готово: знайдено ${data.player_count} гравців за ${data.duration_ms} мс.`);
  } catch (err) {
    setStatus(`Помилка скану: ${err.message}`, true);
  } finally {
    els.scanNowBtn.disabled = false;
  }
}

async function uploadFile(file) {
  if (!file) return;
  setStatus(`Сканую ${file.name}...`);
  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "Upload failed");
    }
    renderScan(data);
    await fetchLatest();
    setStatus(`Файл проскановано: ${data.player_count} гравців.`);
  } catch (err) {
    setStatus(`Помилка завантаження файлу: ${err.message}`, true);
  }
}

function connectSSE() {
  const ev = new EventSource("/api/events");
  ev.onopen = () => {
    els.sseStatus.dataset.state = "connected";
    els.sseLabel.textContent = "Live (очікування шорткату)";
  };
  ev.onerror = () => {
    els.sseStatus.dataset.state = "error";
    els.sseLabel.textContent = "Перепідключення...";
  };
  ev.addEventListener("scan", (event) => {
    const scan = JSON.parse(event.data);
    renderScan(scan);
    if (scan.scan_status === "loading_steam") {
      const total = scan.progress?.total ?? scan.player_count ?? 0;
      setStatus(`OCR готово (${total} гравців). Тягну профілі Steam / Leetify...`);
    } else if (scan.scan_status === "loading_ai") {
      setStatus("Профілі завантажено. Запускаю AI-аналіз (Claude)...");
    } else {
      fetchLatest();
      setStatus(`Скан завершено: ${scan.player_count} гравців (${scan.duration_ms} мс).`);
    }
  });
  ev.addEventListener("scan_update", (event) => {
    const payload = JSON.parse(event.data);
    if (!state.currentScan || state.currentScan.id !== payload.scan_id) return;
    const players = state.currentScan.players || [];
    const idx = players.findIndex((p) => p.nick === payload.player?.nick);
    if (idx !== -1) {
      players[idx] = payload.player;
    }
    state.currentScan.progress = payload.progress;
    state.currentScan.scan_status = payload.scan_status;
    renderScan(state.currentScan);
    if (payload.progress) {
      setStatus(`Завантаження профілів: ${payload.progress.done} / ${payload.progress.total}...`);
    }
  });
}

// Sortable table headers
els.playersTable.querySelectorAll("thead th[data-key] > button").forEach((btn) => {
  btn.addEventListener("click", () => {
    const th = btn.closest("th");
    const key = th.dataset.key;
    const nextDir =
      state.sortKey === key && state.sortDir === "descending" ? "ascending" : "descending";
    state.sortKey = key;
    state.sortDir = nextDir;

    els.playersTable.querySelectorAll("thead th[aria-sort]").forEach((h) => {
      h.removeAttribute("aria-sort");
    });
    th.setAttribute("aria-sort", nextDir);

    if (state.currentScan) {
      renderScan(state.currentScan);
    }
  });
});

els.scanNowBtn.addEventListener("click", triggerScan);
els.rescanBtn.addEventListener("click", async () => {
  if (!state.currentScan) return;
  els.rescanBtn.disabled = true;
  setStatus("Проводжу повторний аналіз поточного скріншоту...");
  try {
    const res = await fetch("/api/rescan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_id: state.currentScan.id }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Rescan failed");
    renderScan(data);
    await fetchLatest();
    setStatus(`Повторний аналіз завершено: ${data.player_count} гравців за ${data.duration_ms} мс.`);
  } catch (err) {
    setStatus(`Помилка повторного аналізу: ${err.message}`, true);
  } finally {
    els.rescanBtn.disabled = !state.currentScan;
  }
});
els.fileInput.addEventListener("change", (e) => {
  if (els.toolsMenu) els.toolsMenu.open = false;
  const file = e.target.files?.[0];
  if (file) uploadFile(file);
  e.target.value = "";
});

// Close tools dropdown on outside click, Escape, or keyboard activation on file label
if (els.toolsMenu) {
  document.addEventListener("click", (e) => {
    if (els.toolsMenu.open && !els.toolsMenu.contains(e.target)) {
      els.toolsMenu.open = false;
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && els.toolsMenu.open) {
      els.toolsMenu.open = false;
      els.toolsMenu.querySelector("summary")?.focus();
    }
  });
  const fileLabel = els.toolsMenu.querySelector(".file-btn");
  if (fileLabel) {
    fileLabel.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        els.fileInput.click();
      }
    });
  }
}

els.historySelect.addEventListener("change", async (e) => {
  const id = e.target.value;
  if (!id) return;
  const res = await fetch(`/api/scan/${encodeURIComponent(id)}`);
  if (res.ok) {
    renderScan(await res.json());
  }
});

// Drag and drop screenshot support
els.dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  els.dropZone.classList.add("drag-over");
});
els.dropZone.addEventListener("dragleave", () => {
  els.dropZone.classList.remove("drag-over");
});
els.dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  els.dropZone.classList.remove("drag-over");
  const file = e.dataTransfer?.files?.[0];
  if (file) uploadFile(file);
});

els.playersBody.addEventListener("click", async (e) => {
  const aiBtn = e.target.closest(".run-ai-player-btn");
  if (aiBtn && state.currentScan) {
    const nick = aiBtn.dataset.nick;
    aiBtn.disabled = true;
    aiBtn.textContent = "Аналізую...";
    setStatus(`Запускаю AI-аналіз для ${nick}...`);
    try {
      const res = await fetch("/api/ai-analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scan_id: state.currentScan.id, nick }),
      });
      const updated = await res.json();
      if (!res.ok) throw new Error(updated.error || "AI analysis failed");
      renderScan(updated);
      setStatus(`AI-аналіз для ${nick} завершено.`);
    } catch (err) {
      setStatus(`Помилка AI-аналізу: ${err.message}`, true);
      aiBtn.disabled = false;
      aiBtn.textContent = "Запустити AI-аналіз (Claude)";
    }
    return;
  }

  const btn = e.target.closest(".edit-nick-btn");
  if (!btn) {
    if (!e.target.closest("a, button, input, form")) {
      const row = e.target.closest("tr.player-row");
      if (row && row.dataset.nick && state.currentScan) {
        const nick = row.dataset.nick;
        const nextRow = row.nextElementSibling;
        const hasDetailsRow = nextRow && nextRow.classList.contains("player-details-row");
        if (state.expandedNicks.has(nick)) {
          state.expandedNicks.delete(nick);
          row.classList.remove("expanded");
          if (hasDetailsRow) nextRow.remove();
        } else {
          state.expandedNicks.add(nick);
          row.classList.add("expanded");
          if (!hasDetailsRow) {
            const player = (state.currentScan.players || []).find((p) => p.nick === nick);
            if (player) {
              row.insertAdjacentHTML("afterend", renderPlayerDetailsRow(player));
            }
          }
        }
      }
    }
    return;
  }
  if (!state.currentScan) return;
  const namesBox = btn.closest(".player-names");
  const existing = namesBox.querySelector(".recheck-form");
  if (existing) {
    existing.remove();
    return;
  }
  const oldNick = btn.dataset.nick;
  const form = document.createElement("form");
  form.className = "recheck-form";
  form.innerHTML = `
    <input class="recheck-input" type="text" value="${escapeHtml(oldNick)}" placeholder="Нік, SteamID64 або URL" />
    <button type="submit" class="btn btn-primary recheck-submit">Знайти</button>
  `;
  namesBox.appendChild(form);
  const input = form.querySelector(".recheck-input");
  input.focus();
  input.select();

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    const submitBtn = form.querySelector(".recheck-submit");
    submitBtn.disabled = true;
    setStatus(`Шукаю профіль для "${query}"...`);
    try {
      const res = await fetch("/api/recheck", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scan_id: state.currentScan.id,
          old_nick: oldNick,
          query,
        }),
      });
      const updated = await res.json();
      if (!res.ok) throw new Error(updated.error || "Recheck failed");
      renderScan(updated);
      setStatus(`Оновлено профіль для "${query}".`);
    } catch (err) {
      setStatus(`Помилка пошуку: ${err.message}`, true);
      submitBtn.disabled = false;
    }
  });
});

function renderLogs(scan) {
  const players = scan?.players || [];
  if (!players.length) {
    els.logsContainer.innerHTML = '<p class="logs-empty">Немає даних сканування.</p>';
    return;
  }

  els.logsContainer.innerHTML = players
    .map((p) => {
      const steam = p.steam || {};
      const logs = (steam.search_log || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
      const cands = steam.candidates || [];
      const candHtml = cands.length
        ? `<div class="cand-list">${cands
            .map((c) => {
              const st = c.in_cs2 ? "In CS2" : c.online_state || "offline";
              const avPct = Math.round((c.avatar_match || 0) * 100);
              const hrs = c.cs2_hours != null ? ` · ${Math.round(c.cs2_hours)}h` : "";
              const isSelected = steam.steamid64 && c.steamid64 === steam.steamid64;
              const imgHtml = c.avatar_url
                ? `<img src="${escapeHtml(c.avatar_url)}" alt="" loading="lazy" />`
                : "";
              return `
                <div class="cand-row">
                  <div class="cand-left">
                    ${imgHtml}
                    <div>
                      <div><a href="${escapeHtml(c.profile_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(
                c.profile_name || "?"
              )}</a></div>
                      <div class="cand-meta">${escapeHtml(st)} · av ${avPct}%${hrs} · score ${c.score}</div>
                    </div>
                  </div>
                  <button type="button" class="btn btn-secondary recheck-submit pick-cand-btn"
                    data-old-nick="${escapeHtml(p.nick)}"
                    data-url="${escapeHtml(c.profile_url)}"
                    ${isSelected ? "disabled" : ""}>
                    ${isSelected ? "Обрано" : "Вибрати"}
                  </button>
                </div>`;
            })
            .join("")}</div>`
        : '<div class="cand-meta">Кандидатів не знайдено</div>';

      return `
        <article class="log-card">
          <div class="log-card-head">
            <strong>${escapeHtml(p.nick)}</strong>
            <span class="cand-meta">${escapeHtml(steam.profile_name || "Не знайдено")}</span>
          </div>
          ${logs ? `<ul class="log-steps">${logs}</ul>` : ""}
          ${candHtml}
        </article>`;
    })
    .join("");
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.tab;
    document.querySelectorAll(".tab-btn").forEach((b) => {
      const active = b.dataset.tab === target;
      b.classList.toggle("active", active);
      b.setAttribute("aria-selected", String(active));
    });
    els.tabPanelTable.hidden = target !== "table";
    els.tabPanelLogs.hidden = target !== "logs";
  });
});

els.logsContainer.addEventListener("click", async (e) => {
  const btn = e.target.closest(".pick-cand-btn");
  if (!btn || !state.currentScan) return;
  btn.disabled = true;
  const oldNick = btn.dataset.oldNick;
  const url = btn.dataset.url;
  setStatus(`Оновлюю профіль для "${oldNick}"...`);
  try {
    const res = await fetch("/api/recheck", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_id: state.currentScan.id, old_nick: oldNick, query: url }),
    });
    const updated = await res.json();
    if (!res.ok) throw new Error(updated.error || "Recheck failed");
    renderScan(updated);
    setStatus(`Обрано профіль для "${oldNick}".`);
  } catch (err) {
    setStatus(`Помилка: ${err.message}`, true);
    btn.disabled = false;
  }
});

els.searchInput.addEventListener("input", (e) => {
  state.searchQuery = e.target.value;
  if (state.currentScan) renderScan(state.currentScan);
});

els.riskFilter.addEventListener("change", (e) => {
  state.riskFilter = e.target.value;
  saveLobbyPrefs();
  if (state.currentScan) renderScan(state.currentScan);
});

els.onlyWithDataFilter.addEventListener("change", (e) => {
  state.onlyWithData = e.target.checked;
  saveLobbyPrefs();
  if (state.currentScan) renderScan(state.currentScan);
});

els.onlyHighRiskFilter.addEventListener("change", (e) => {
  state.onlyHighRisk = e.target.checked;
  saveLobbyPrefs();
  if (state.currentScan) renderScan(state.currentScan);
});

els.dateFilter.addEventListener("change", async (e) => {
  state.dateFilter = e.target.value;
  state.matchFilter = "";
  updateMatchAndHistorySelects();
  const firstScanId = els.historySelect.value;
  if (firstScanId) {
    const res = await fetch(`/api/scan/${encodeURIComponent(firstScanId)}`);
    if (res.ok) renderScan(await res.json());
  }
});

els.matchSelect.addEventListener("change", async (e) => {
  state.matchFilter = e.target.value;
  updateMatchAndHistorySelects();
  const firstScanId = els.historySelect.value;
  if (firstScanId) {
    const res = await fetch(`/api/scan/${encodeURIComponent(firstScanId)}`);
    if (res.ok) renderScan(await res.json());
  }
});

els.clearFiltersBtn.addEventListener("click", async () => {
  state.searchQuery = "";
  state.dateFilter = "";
  state.matchFilter = "";
  state.riskFilter = "all";
  state.onlyWithData = false;
  state.onlyHighRisk = false;
  els.searchInput.value = "";
  els.dateFilter.value = "";
  els.riskFilter.value = "all";
  els.onlyWithDataFilter.checked = false;
  els.onlyHighRiskFilter.checked = false;
  saveLobbyPrefs();
  updateMatchAndHistorySelects();
  if (state.history.length) {
    const res = await fetch(`/api/scan/${encodeURIComponent(state.history[0].id)}`);
    if (res.ok) renderScan(await res.json());
  }
});

els.setupCs2Btn.addEventListener("click", async () => {
  if (els.toolsMenu) els.toolsMenu.open = false;
  els.setupCs2Btn.disabled = true;
  setStatus("Шукаю CS2 та оновлюю autoexec.cfg...");
  try {
    const res = await fetch("/api/setup-cs2", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Setup failed");
    els.setupStatusText.textContent = data.already_present
      ? `Бінд уже є в ${data.autoexec_path}`
      : `Додано '${data.bind_line}' у ${data.autoexec_path}`;
    els.launchOptionsCode.textContent = data.launch_options;
    els.copyLaunchBtn.textContent = "Копіювати";
    els.cs2SetupDialog.showModal();
    setStatus("autoexec.cfg налаштовано.");
  } catch (err) {
    setStatus(`Помилка налаштування CS2: ${err.message}`, true);
  } finally {
    els.setupCs2Btn.disabled = false;
  }
});

els.copyLaunchBtn.addEventListener("click", async () => {
  await navigator.clipboard.writeText(els.launchOptionsCode.textContent);
  els.copyLaunchBtn.textContent = "Скопійовано!";
});

els.closeSetupBtn.addEventListener("click", () => {
  els.cs2SetupDialog.close();
});

let pendingClearSteamKey = false;
let pendingClearFaceitKey = false;
let pendingClearAnthropicKey = false;

function populateSettingsDialog(data) {
  const cfg = data.config || {};
  const resolved = data.resolved || {};
  const weights = cfg.ai_weights || {};
  els.cfgTabDelay.value = cfg.tab_delay_sec ?? 0.5;
  els.cfgTabCooldown.value = cfg.tab_cooldown_sec ?? 4.0;
  els.cfgCs2Only.checked = cfg.cs2_only !== false;
  els.cfgNotifyEnabled.checked = cfg.notify_enabled !== false;
  els.cfgCs2Dir.value = cfg.cs2_game_dir || "";
  els.cfgConsoleLog.value = cfg.console_log_path || "";
  els.cfgScreenshotsDir.value = cfg.screenshots_dir || "";
  els.cfgScreenshotTool.value = cfg.screenshot_tool || "auto";
  els.cfgSteamApiKey.value = "";
  pendingClearSteamKey = false;
  if (els.cfgFaceitApiKey) els.cfgFaceitApiKey.value = "";
  pendingClearFaceitKey = false;

  els.cfgAiEnabled.checked = Boolean(cfg.ai_enabled);
  els.cfgAiProvider.value = cfg.ai_provider || "claude_cli";
  els.cfgAiModel.value = cfg.ai_model || "sonnet";
  els.cfgWeightScoreboard.value = weights.scoreboard ?? 0.30;
  els.cfgWeightLeetify.value = weights.leetify ?? 0.40;
  els.cfgWeightSteam.value = weights.steam_account ?? 0.30;
  els.cfgAiPrompt.value = cfg.ai_prompt || "";
  els.cfgAnthropicApiKey.value = "";
  pendingClearAnthropicKey = false;

  els.cfgClaudeCliStatus.textContent = resolved.claude_cli_available
    ? "Claude CLI знайдено"
    : "Claude CLI не знайдено в PATH";

  if (data.anthropic_api_key_source === "config") {
    els.cfgAnthropicKeyStatus.textContent = `(збережено: ${data.anthropic_api_key_masked})`;
  } else if (data.anthropic_api_key_source === "env") {
    els.cfgAnthropicKeyStatus.textContent = "(зі змінної середовища)";
  } else {
    els.cfgAnthropicKeyStatus.textContent = "(не задано)";
  }

  els.cfgCs2DirHint.textContent = resolved.cs2_game_dir
    ? `Зараз використовується: ${resolved.cs2_game_dir}`
    : "Автовизначення: папку CS2 не знайдено";
  els.cfgConsoleLogHint.textContent = resolved.console_log_path
    ? `Зараз використовується: ${resolved.console_log_path}`
    : "";
  els.cfgScreenshotsDirHint.textContent = resolved.screenshots_dir
    ? `Зараз використовується: ${resolved.screenshots_dir}`
    : "";

  if (data.steam_api_key_source === "config") {
    els.cfgSteamKeyStatus.textContent = `(збережено в config.json: ${data.steam_api_key_masked})`;
  } else if (data.steam_api_key_source === "env") {
    els.cfgSteamKeyStatus.textContent = "(підключено зі змінної середовища)";
  } else {
    els.cfgSteamKeyStatus.textContent = "(не задано)";
  }
  if (els.cfgFaceitKeyStatus) {
    if (data.faceit_api_key_source === "config") {
      els.cfgFaceitKeyStatus.textContent = `(збережено в config.json: ${data.faceit_api_key_masked})`;
    } else if (data.faceit_api_key_source === "env") {
      els.cfgFaceitKeyStatus.textContent = "(підключено зі змінної середовища)";
    } else {
      els.cfgFaceitKeyStatus.textContent = "(не задано)";
    }
  }
  els.settingsSaveMsg.textContent = "";
  els.settingsSaveMsg.classList.remove("error");
}

els.settingsBtn?.addEventListener("click", async () => {
  if (els.toolsMenu) els.toolsMenu.open = false;
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Не вдалося завантажити конфіг");
    populateSettingsDialog(data);
    els.settingsDialog.showModal();
  } catch (err) {
    setStatus(`Помилка відкриття налаштувань: ${err.message}`, true);
  }
});

els.resetPathsBtn?.addEventListener("click", () => {
  els.cfgCs2Dir.value = "";
  els.cfgConsoleLog.value = "";
  els.cfgScreenshotsDir.value = "";
  els.settingsSaveMsg.textContent = "Поля шляхів очищено — натисніть «Зберегти» для автовизначення.";
  els.settingsSaveMsg.classList.remove("error");
});

els.clearSteamKeyBtn?.addEventListener("click", () => {
  els.cfgSteamApiKey.value = "";
  pendingClearSteamKey = true;
  els.cfgSteamKeyStatus.textContent = "(буде видалено з config.json після збереження)";
});

els.clearFaceitKeyBtn?.addEventListener("click", () => {
  if (els.cfgFaceitApiKey) els.cfgFaceitApiKey.value = "";
  pendingClearFaceitKey = true;
  if (els.cfgFaceitKeyStatus) {
    els.cfgFaceitKeyStatus.textContent = "(буде видалено з config.json після збереження)";
  }
});

els.clearAnthropicKeyBtn?.addEventListener("click", () => {
  els.cfgAnthropicApiKey.value = "";
  pendingClearAnthropicKey = true;
  els.cfgAnthropicKeyStatus.textContent = "(буде видалено з config.json після збереження)";
});

els.aiAnalyzeScanBtn?.addEventListener("click", async () => {
  if (els.toolsMenu) els.toolsMenu.open = false;
  if (!state.currentScan) {
    setStatus("Спочатку зробіть або завантажте скан.", true);
    return;
  }
  els.aiAnalyzeScanBtn.disabled = true;
  setStatus("Запускаю AI-аналіз (Claude) для всіх гравців...");
  try {
    const res = await fetch("/api/ai-analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_id: state.currentScan.id }),
    });
    const updated = await res.json();
    if (!res.ok) throw new Error(updated.error || "AI analysis failed");
    renderScan(updated);
    setStatus("AI-аналіз (Claude) завершено.");
  } catch (err) {
    setStatus(`Помилка AI-аналізу: ${err.message}`, true);
  } finally {
    els.aiAnalyzeScanBtn.disabled = false;
  }
});

els.settingsForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  els.saveSettingsBtn.disabled = true;
  els.settingsSaveMsg.textContent = "Збереження...";
  els.settingsSaveMsg.classList.remove("error");
  try {
    const payload = {
      tab_delay_sec: parseFloat(els.cfgTabDelay.value),
      tab_cooldown_sec: parseFloat(els.cfgTabCooldown.value),
      cs2_only: els.cfgCs2Only.checked,
      notify_enabled: els.cfgNotifyEnabled.checked,
      cs2_game_dir: els.cfgCs2Dir.value.trim(),
      console_log_path: els.cfgConsoleLog.value.trim(),
      screenshots_dir: els.cfgScreenshotsDir.value.trim(),
      screenshot_tool: els.cfgScreenshotTool.value,
      steam_api_key: els.cfgSteamApiKey.value.trim(),
      clear_steam_api_key: pendingClearSteamKey,
      faceit_api_key: els.cfgFaceitApiKey ? els.cfgFaceitApiKey.value.trim() : "",
      clear_faceit_api_key: pendingClearFaceitKey,
      ai_enabled: els.cfgAiEnabled.checked,
      ai_provider: els.cfgAiProvider.value,
      ai_model: els.cfgAiModel.value,
      ai_weights: {
        scoreboard: parseFloat(els.cfgWeightScoreboard.value),
        leetify: parseFloat(els.cfgWeightLeetify.value),
        steam_account: parseFloat(els.cfgWeightSteam.value),
      },
      ai_prompt: els.cfgAiPrompt.value.trim(),
      anthropic_api_key: els.cfgAnthropicApiKey.value.trim(),
      clear_anthropic_api_key: pendingClearAnthropicKey,
    };
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Помилка збереження");
    populateSettingsDialog(data);
    els.settingsSaveMsg.textContent = "Збережено і застосовано.";
    setStatus(`Налаштування оновлено (затримка TAB: ${data.config.tab_delay_sec}с).`);
  } catch (err) {
    els.settingsSaveMsg.textContent = err.message;
    els.settingsSaveMsg.classList.add("error");
  } finally {
    els.saveSettingsBtn.disabled = false;
  }
});

els.closeSettingsXBtn?.addEventListener("click", () => els.settingsDialog.close());
els.cancelSettingsBtn?.addEventListener("click", () => els.settingsDialog.close());

els.helpBtn?.addEventListener("click", () => {
  if (els.toolsMenu) els.toolsMenu.open = false;
  els.helpDialog.showModal();
});
els.closeHelpBtn?.addEventListener("click", () => els.helpDialog.close());
els.closeHelpXBtn?.addEventListener("click", () => els.helpDialog.close());

loadLobbyPrefs();
fetchLatest();
connectSSE();
