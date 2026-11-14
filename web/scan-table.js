import { attentionReason, candidateStatusText, renderAttentionCard, renderConfidentRow, showMainTab } from "/attention.js";
import { applyPlayerFilters, players_ready_for_ai, renderLobbySummary, saveLobbyPrefs, sortPlayers, updateRateLimitBanner } from "/lobby.js";
import { renderPlayerDetailsRow } from "/player-details.js";
import { playerDataSources } from "/sources.js";
import { RISK_LABELS, els, state } from "/state.js";
import { escapeHtml, formatDuration, playerKey, uidAttr } from "/util.js";

export function renderScan(scan) {
  state.currentScan = scan;
  if (els.rescanBtn) {
    els.rescanBtn.disabled = !scan;
  }
  if (!scan) {
    return;
  }

  const players = scan.players || [];
  renderLobbySummary(players);
  updateRateLimitBanner(players);
  const suspects = players.filter((p) => p.cheat_probability >= 55);
  const top = [...players].sort((a, b) => b.cheat_probability - a.cheat_probability)[0];

  els.statPlayers.textContent = String(players.length);
  els.statSuspects.textContent = String(suspects.length);
  els.statSusLabel.textContent = "Ризик ≥55%";
  els.statSusLabel?.closest(".risk-indicator")?.classList.toggle("is-zero", suspects.length === 0);
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
    els.tabCountLogs.textContent = String(players.filter((p) => attentionReason(p)).length);
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
        `<button type="button" class="btn ${cls} btn-sm run-ai-player-btn" data-nick="${escapeHtml(p.nick)}"${uidAttr(p)}>${label}</button>`;
      const aiBadge = ai
        ? `<div class="ai-row-actions"><span class="ai-decision-badge ${escapeHtml(aiClass)}">AI-DECISION: ${escapeHtml(ai.decision)} (${escapeHtml(ai.confidence)}%)</span>${aiRunBtn("↻ Перезапустити AI", "btn-secondary")}</div>`
        : p.loading_stage === "ai"
          ? `<div><span class="ai-loading-pill"><span class="spinner-dot" aria-hidden="true"></span>AI аналізує...</span></div>`
          : isLoading || !players_ready_for_ai(p)
            ? ""
            : `<div class="ai-row-actions">${aiRunBtn("Запустити AI-аналіз", "btn-primary")}</div>`;
      const isExpanded = state.expandedNicks.has(playerKey(p));

      const mainRow = `
        <tr class="player-row ${isExpanded ? "expanded" : ""}" data-nick="${escapeHtml(p.nick)}"${uidAttr(p)}>
          <td data-label="Гравець">
            <div class="player-cell">
              <span class="expand-caret" aria-hidden="true">▸</span>
              ${avatarHtml}
              <div class="player-names">
                <div class="nick-row">
                  <span class="nick-main">${escapeHtml(p.nick)}</span>
                  ${attentionReason(p) ? `<button type="button" class="attention-flag" data-nick="${escapeHtml(p.nick)}" title="Потребує уваги: ${escapeHtml(attentionReason(p))}">⚠</button>` : ""}
                  <button type="button" class="edit-nick-btn" data-nick="${escapeHtml(p.nick)}"${uidAttr(p)} title="Виправити нік або вставити Steam URL / SteamID64">✎</button>
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
            <div class="reasons-text">${reasons}</div>
            ${aiBadge}
          </td>
          <td data-label="Дані" class="sources-cell">
            <div class="source-badges">
              ${(() => {
                const sources = playerDataSources(p);
                const recheck = (key, letter, label, ok) =>
                  `<button type="button" class="source-badge source-recheck ${ok ? `source-${key}` : "source-none"}" data-nick="${escapeHtml(p.nick)}" data-source="${key}" title="${label}: ${ok ? "є дані" : "немає даних"}. Натисніть, щоб перевірити повторно">${letter}</button>`;
                const cs2 = sources.cs2
                  ? '<span class="source-badge source-cs2" title="Steam CS2: дані є">S</span>'
                  : '<span class="source-badge source-none" title="Steam CS2: немає даних">S</span>';
                return recheck("leetify", "L", "Leetify", sources.leetify) + recheck("cstracker", "C", "CSTracker", sources.cstracker) + cs2;
              })()}
            </div>
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

export function updateMatchAndHistorySelects() {
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

export function renderLogs(scan) {
  const players = scan?.players || [];
  if (!players.length) {
    els.logsContainer.innerHTML = '<p class="logs-empty">Немає даних сканування.</p>';
    return;
  }
  const attention = players.filter((p) => attentionReason(p));
  const confident = players.filter((p) => !attentionReason(p));
  const attentionHtml = attention.length
    ? attention.map(renderAttentionCard).join("")
    : '<p class="logs-empty">Усі профілі знайдено з упевненим збігом.</p>';
  const confidentHtml = confident.length
    ? `<details class="confident-block"><summary>Упевнені збіги (${confident.length})</summary><div class="confident-list">${confident.map(renderConfidentRow).join("")}</div></details>`
    : "";
  els.logsContainer.innerHTML = `<div class="triage-list">${attentionHtml}</div>${confidentHtml}`;
}

export function openNickEditor(nick) {
  showMainTab("table");
  const find = () => [...els.playersBody.querySelectorAll(".edit-nick-btn")].find((b) => b.dataset.nick === nick);
  let editBtn = find();
  if (!editBtn && state.currentScan) {
    state.riskFilter = "all";
    els.riskFilter.value = "all";
    saveLobbyPrefs();
    renderScan(state.currentScan);
    editBtn = find();
  }
  if (!editBtn) return;
  editBtn.closest("tr")?.scrollIntoView({ block: "center" });
  editBtn.click();
}

export function openAvatarCompare(nick, steamid64) {
  const p = (state.currentScan?.players || []).find((x) => x.nick === nick);
  const c = (p?.steam?.candidates || []).find((x) => x.steamid64 === steamid64);
  if (!p || !c) return;
  const shotImg = document.getElementById("compareShot");
  const candImg = document.getElementById("compareCand");
  const shot = p.steam?.screenshot_avatar;
  shotImg.hidden = !shot;
  if (shot) shotImg.src = shot;
  candImg.hidden = !c.avatar_url;
  if (c.avatar_url) candImg.src = c.avatar_url;
  document.getElementById("compareTitle").textContent = `${p.nick} ↔ ${c.profile_name || "?"}`;
  document.getElementById("compareShotNote").textContent = shot ? "" : "Аватар зі скріншота не збережено";
  const facts = [
    `збіг ${Math.round((c.avatar_match || 0) * 100)}%`,
    candidateStatusText(c),
    c.account_age_years != null ? `акаунт ${c.account_age_years} р.` : null,
    c.cs2_hours != null ? `${Math.round(c.cs2_hours)} год у CS2` : null,
    c.vac_banned ? "VAC" : null,
  ].filter(Boolean);
  document.getElementById("compareMeta").textContent = facts.join(" · ");
  document.getElementById("avatarCompareDialog").showModal();
}

export function focusedPlayerRow() {
  return document.activeElement?.closest?.("tr.player-row") || null;
}

export function moveRowFocus(delta) {
  const rows = Array.from(els.playersBody.querySelectorAll("tr.player-row"));
  if (!rows.length) return;
  const cur = rows.indexOf(focusedPlayerRow());
  const next = cur < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, cur + delta));
  rows[next].tabIndex = -1; // програмний фокус; порядок Tab не змінюємо
  rows[next].focus();
  rows[next].scrollIntoView({ block: "nearest" });
}

