import { renderSourceStrip, sourceTabsFor } from "/sources.js";
import { state } from "/state.js";
import { detailKv, detailSection, emptyTabText, escapeHtml, fmtNum, playerKey, uidAttr, wrapTabCard } from "/util.js";

export const OVERVIEW_TAB = { key: "overview", label: "Огляд" };
export const STEAM_TAB = { key: "steam", label: "Steam" };
export const AI_TAB = { key: "ai", label: "AI" };

// Tabs of one player: Overview, the source tabs (built-in first, then generic), Steam, AI.
export function tabsFor(p) {
  return [OVERVIEW_TAB, ...sourceTabsFor(p), STEAM_TAB, AI_TAB];
}

export function getPlayerTab(p) {
  const key = state.playerTabs.get(playerKey(p));
  return tabsFor(p).some((t) => t.key === key) ? key : "overview";
}

export function selectPlayerTab(btn, focus = false) {
  const key = btn.dataset.tabKey;
  state.playerTabs.set(btn.dataset.playerUid || btn.dataset.nick, key);
  const row = btn.closest(".player-details-row");
  if (!row) return;
  row.querySelectorAll('[role="tab"]').forEach((t) => {
    const sel = t === btn;
    t.setAttribute("aria-selected", String(sel));
    t.tabIndex = sel ? 0 : -1;
  });
  row.querySelectorAll('[role="tabpanel"]').forEach((panel) => {
    panel.hidden = panel.dataset.tabKey !== key;
  });
  if (focus) btn.focus();
}

export function renderPlayerTabs(p, panels) {
  const uid = `pt-${String(p.nick || "").replace(/[^\w-]/g, "_")}`;
  const tabs = tabsFor(p);
  const active = getPlayerTab(p);
  const buttons = tabs.map((t) => {
    const sel = t.key === active;
    return `<button type="button" role="tab" class="player-tab" id="${uid}-tab-${t.key}" aria-controls="${uid}-panel-${t.key}" aria-selected="${sel}" tabindex="${sel ? 0 : -1}" data-nick="${escapeHtml(p.nick)}"${uidAttr(p)} data-tab-key="${t.key}">${escapeHtml(t.label)}</button>`;
  }).join("");
  const body = tabs.map((t) => {
    const sel = t.key === active;
    return `<div role="tabpanel" class="player-tab-panel" id="${uid}-panel-${t.key}" aria-labelledby="${uid}-tab-${t.key}" data-tab-key="${t.key}"${sel ? "" : " hidden"}>${panels[t.key] || ""}</div>`;
  }).join("");
  return `<div class="player-tabs" role="tablist" aria-label="Розділи аналізу гравця">${buttons}</div>${body}`;
}

export function renderOverviewPanel(p) {
  const steam = p.steam || {};
  const lf = steam.leetify || {};
  const cs2 = steam.cs2_stats || {};
  const fc = steam.faceit || {};
  const ct = (steam.extra_stats || {}).cstracker || {};
  const rk = lf.ranks || {};
  const ew = p.effective_weights || {};
  const weightsText =
    ew.scoreboard != null || ew.leetify != null || ew.steam_account != null
      ? `Рахунок: ${Math.round((ew.scoreboard ?? 0) * 100)}%, Leetify/FACEIT: ${Math.round((ew.leetify ?? 0) * 100)}%, Steam: ${Math.round((ew.steam_account ?? 0) * 100)}%`
      : "—";
  const reasons = (p.reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join("");
  return [
    detailSection("Оцінка", [
      detailKv("Ймовірність чітера / смурфа", `${Math.round(p.cheat_probability ?? 0)}% / ${Math.round(p.smurf_probability ?? 0)}%`),
      detailKv("Невизначеність", `${Math.round(p.uncertainty ?? 50)}%`),
      detailKv("Ваги джерел", weightsText),
    ]),
    detailSection("Ключові цифри", [
      detailKv("Рейтинг Leetify", fmtNum(rk.leetify ?? lf.leetify_rating, 2)),
      detailKv("CS2 K/D / HS%", cs2.kd != null ? `${Number(cs2.kd).toFixed(2)} / ${fmtNum(cs2.hs_pct, 1, "%")}` : null),
      detailKv("Годин у CS2", steam.cs2_hours != null ? `${steam.cs2_hours} год` : null),
      detailKv("FACEIT ELO", fc.faceit_elo),
      detailKv("CSTracker trust", ct.trust != null ? `${Number(ct.trust).toFixed(1)}%` : null),
    ]),
    `<div class="detail-section"><h5>Причини</h5>${
      reasons ? `<ul class="detail-reasons">${reasons}</ul>` : emptyTabText("Причин для підозри не зафіксовано.")
    }</div>`,
  ].join("");
}

export function renderSteamPanel(p) {
  const steam = p.steam || {};
  if (!Object.keys(steam).length) return emptyTabText("Дані Steam ще не отримано для цього гравця.");
  const fc = steam.faceit || {};
  const cs2 = steam.cs2_stats || {};
  return (
    detailSection("Steam", [
      detailKv("SteamID64", steam.steamid64),
      detailKv("Вік акаунта", steam.account_age_years != null ? `${steam.account_age_years} р.` : null),
      detailKv("Годин у CS2", steam.cs2_hours != null ? `${steam.cs2_hours} год` : null),
      detailKv("Рівень Steam", steam.steam_level),
      detailKv("VAC / Game бани", `${steam.number_of_vac_bans || 0} VAC / ${steam.game_bans || 0} Game`),
      detailKv("CS2 K/D / HS%", cs2.kd != null ? `${Number(cs2.kd).toFixed(2)} / ${fmtNum(cs2.hs_pct, 1, "%")}` : null),
      detailKv("CS2 вбивства / матчі", cs2.total_kills != null ? `${cs2.total_kills} / ${cs2.total_matches_played ?? "—"}` : null),
      detailKv("Метод пошуку", steam.lookup_method),
    ]) +
    (fc.found || fc.skill_level != null
      ? detailSection("FACEIT", [
          detailKv("Рівень / ELO", `${fc.skill_level ?? "—"} / ${fc.faceit_elo ?? "—"}`),
          detailKv("K/D / WR / Матчі", `${fc.kd != null ? Number(fc.kd).toFixed(2) : "—"} / ${fmtNum(fc.winrate, 1, "%") ?? "—"} / ${fc.matches ?? "—"}`),
        ])
      : "")
  );
}

export function renderAiPanel(p) {
  const ai = p.ai_analysis || null;
  const aiProbsNote =
    ai && (ai.cheat_probability != null || ai.smurf_probability != null)
      ? `<div class="ai-meta-note">Чітер: ${escapeHtml(ai.cheat_probability ?? "—")}% · Смурф: ${escapeHtml(ai.smurf_probability ?? "—")}%</div>`
      : "";
  return ai
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
        <button type="button" class="btn btn-secondary btn-sm run-ai-player-btn" data-nick="${escapeHtml(p.nick)}"${uidAttr(p)}>Перезапустити AI-аналіз</button>
      </div>`
    : `
      <div class="detail-card-head">
        <h4>AI-аналіз</h4>
      </div>
      <p class="ai-meta-note">AI-аналіз ще не виконувався для цього гравця (можна увімкнути автозапуск у Налаштуваннях або запустити вручну).</p>
      <div>
        <button type="button" class="btn btn-primary btn-sm run-ai-player-btn" data-nick="${escapeHtml(p.nick)}"${uidAttr(p)}>Запустити AI-аналіз (Claude)</button>
      </div>`;
}

export function renderAiLead(p) {
  const ai = p.ai_analysis || null;
  if (ai) {
    const cls = `ai-${escapeHtml(String(ai.decision || "uncertain").toLowerCase())}`;
    return `<div class="ai-lead"><span class="ai-decision-badge ${cls}">AI: ${escapeHtml(ai.decision)} (${escapeHtml(ai.confidence)}%)</span><span class="ai-lead-text">${escapeHtml(ai.summary || "—")}</span></div>`;
  }
  if (p.loading_stage === "ai") {
    return `<div class="ai-lead"><span class="ai-loading-pill"><span class="spinner-dot" aria-hidden="true"></span>AI аналізує...</span></div>`;
  }
  return `<div class="ai-lead ai-lead-empty"><span class="ai-lead-text">AI-висновку ще немає. Запустити можна в рядку гравця або на вкладці AI.</span></div>`;
}

export function renderPlayerDetailsRow(p) {
  const panels = {};
  for (const tab of tabsFor(p)) {
    panels[tab.key] = wrapTabCard(bodyFor(p, tab));
  }
  return `
    <tr class="player-details-row">
      <td colspan="8">
        <div class="player-details-box">
          ${renderAiLead(p)}
          ${renderSourceStrip(p)}
          ${renderPlayerTabs(p, panels)}
        </div>
      </td>
    </tr>`;
}

// Body of one tab: source tabs carry their own render; the three fixed tabs are rendered here.
export function bodyFor(p, tab) {
  if (tab.render) return tab.render(p);
  if (tab.key === "overview") return renderOverviewPanel(p);
  if (tab.key === "steam") return renderSteamPanel(p);
  return renderAiPanel(p);
}
