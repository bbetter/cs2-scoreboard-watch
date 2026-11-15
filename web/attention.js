import { ATTENTION_AVATAR_MIN, ATTENTION_BETTER_GAP, els } from "/state.js";
import { escapeHtml, uidAttr } from "/util.js";

export function attentionReason(p) {
  const steam = p.steam || {};
  const cands = steam.candidates || [];
  if (!cands.length) return "Кандидатів не знайдено";
  const best = cands.reduce((a, b) => ((b.avatar_match || 0) > (a.avatar_match || 0) ? b : a));
  if (!steam.steamid64) return "Профіль не обрано";
  const chosen = cands.find((c) => c.steamid64 === steam.steamid64);
  if (!chosen) return null;
  const confidence = steam.search_confidence || {};
  if (confidence.level === "low") {
    return confidence.margin == null
      ? "Вибрано без точного збігу імені і без сильного збігу аватара"
      : `Непевний вибір: перевага над наступним кандидатом лише ${Math.round(confidence.margin)} балів`;
  }
  const chosenAv = chosen.avatar_match || 0;
  if (chosenAv < ATTENTION_AVATAR_MIN) return `Низький збіг аватара (${Math.round(chosenAv * 100)}%)`;
  if (best.steamid64 !== chosen.steamid64 && (best.avatar_match || 0) - chosenAv > ATTENTION_BETTER_GAP) {
    return `Є кращий збіг: ${Math.round((best.avatar_match || 0) * 100)}%`;
  }
  return null;
}

export function candidateStatusText(c) {
  if (c.in_cs2) return "грає в CS2";
  return c.online_state === "online" ? "онлайн" : "офлайн";
}

export function renderCandidateRow(p, c, selectedId) {
  const isSelected = Boolean(selectedId) && c.steamid64 === selectedId;
  const av = Math.round((c.avatar_match || 0) * 100);
  const hrs = c.cs2_hours != null ? ` · ${Math.round(c.cs2_hours)} год у CS2` : "";
  const age = c.account_age_years != null ? ` · акаунт ${c.account_age_years} р.` : "";
  const vac = c.vac_banned ? " · VAC" : "";
  const img = c.avatar_url
    ? `<img src="${escapeHtml(c.avatar_url)}" alt="" loading="lazy" />`
    : '<span class="cand-noimg" aria-hidden="true"></span>';
  return `
    <div class="cand-row${isSelected ? " is-selected" : ""}">
      <div class="cand-left">
        ${img}
        <div class="cand-text">
          <a href="${escapeHtml(c.profile_url)}" target="_blank" rel="noopener noreferrer" title="Відкрити профіль у Steam">${escapeHtml(c.profile_name || "?")}</a>
          <div class="cand-meta">${candidateStatusText(c)} · збіг аватара ${av}%${hrs}${age}${vac}</div>
        </div>
      </div>
      <div class="cand-actions">
        <button type="button" class="btn btn-secondary recheck-submit compare-cand-btn"
          data-nick="${escapeHtml(p.nick)}" data-steamid64="${escapeHtml(c.steamid64)}"
          ${c.avatar_url ? "" : "disabled"}>Порівняти</button>
        <button type="button" class="btn btn-secondary recheck-submit pick-cand-btn"
          data-old-nick="${escapeHtml(p.nick)}"${uidAttr(p)}
          data-url="${escapeHtml(c.profile_url)}"
          ${isSelected ? "disabled" : ""}>
          ${isSelected ? "Обрано" : "Вибрати"}
        </button>
      </div>
    </div>`;
}

export function renderAttentionCard(p) {
  const steam = p.steam || {};
  const cands = [...(steam.candidates || [])].sort((a, b) => (b.avatar_match || 0) - (a.avatar_match || 0));
  const logs = (steam.search_log || []).map((line) => `<li>${escapeHtml(line)}</li>`).join("");
  return `
    <article class="triage-card" data-nick="${escapeHtml(p.nick)}">
      <header class="triage-head">
        <strong>${escapeHtml(p.nick)}</strong>
        <span class="triage-reason">${escapeHtml(attentionReason(p) || "")}</span>
      </header>
      ${cands.length ? `<div class="cand-list">${cands.map((c) => renderCandidateRow(p, c, steam.steamid64)).join("")}</div>` : ""}
      ${logs ? `<details class="search-log"><summary>Журнал пошуку</summary><ul class="log-steps">${logs}</ul></details>` : ""}
      <div class="triage-foot">
        <button type="button" class="btn btn-secondary btn-sm open-nick-editor-btn" data-nick="${escapeHtml(p.nick)}">Це не він? Ввести нік, SteamID або URL</button>
      </div>
    </article>`;
}

export function renderConfidentRow(p) {
  const steam = p.steam || {};
  const name = escapeHtml(steam.profile_name || "—");
  const who = steam.profile_url
    ? `<a href="${escapeHtml(steam.profile_url)}" target="_blank" rel="noopener noreferrer">${name}</a>`
    : name;
  return `<div class="confident-row"><strong>${escapeHtml(p.nick)}</strong><span>${who}</span></div>`;
}

export function showMainTab(target) {
  document.querySelectorAll(".tab-btn").forEach((b) => {
    const active = b.dataset.tab === target;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", String(active));
  });
  els.tabPanelTable.hidden = target !== "table";
  els.tabPanelLogs.hidden = target !== "logs";
}

