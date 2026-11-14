import { connectSSE, fetchLatest, retrySource, triggerScan, uploadFile } from "/api.js";
import { showMainTab } from "/attention.js";
import { loadLobbyPrefs, saveLobbyPrefs } from "/lobby.js";
import { renderPlayerDetailsRow, selectPlayerTab } from "/player-details.js";
import { focusedPlayerRow, moveRowFocus, openAvatarCompare, openNickEditor, renderScan, updateMatchAndHistorySelects } from "/scan-table.js";
import { populateSettingsDialog } from "/settings.js";
import { SHORTCUT_HINT, els, state } from "/state.js";
import { escapeHtml, playerKey, setStatus } from "/util.js";

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

els.playersBody.addEventListener("keydown", (e) => {
  const tab = e.target.closest?.('[role="tab"]');
  if (!tab) return;
  const tabs = [...tab.parentElement.querySelectorAll('[role="tab"]')];
  const idx = tabs.indexOf(tab);
  let next = null;
  if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
  else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
  else if (e.key === "Home") next = 0;
  else if (e.key === "End") next = tabs.length - 1;
  if (next == null) return;
  e.preventDefault();
  selectPlayerTab(tabs[next], true);
});

els.playersBody.addEventListener("click", async (e) => {
  const flag = e.target.closest(".attention-flag");
  if (flag) {
    showMainTab("logs");
    document.querySelector(`.triage-card[data-nick="${CSS.escape(flag.dataset.nick)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const tabBtn = e.target.closest(".player-tab");
  if (tabBtn) {
    selectPlayerTab(tabBtn);
    return;
  }
  const aiBtn = e.target.closest(".run-ai-player-btn");
  if (aiBtn && state.currentScan) {
    const nick = aiBtn.dataset.nick;
    const uid = aiBtn.dataset.playerUid;
    aiBtn.disabled = true;
    aiBtn.textContent = "Аналізую...";
    setStatus(`Запускаю AI-аналіз для ${nick}...`);
    try {
      const res = await fetch("/api/ai-analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scan_id: state.currentScan.id, nick, ...(uid ? { player_uid: uid } : {}) }),
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

  const retryBtn = e.target.closest(".source-retry-btn, .source-recheck");
  if (retryBtn && state.currentScan) {
    retrySource(retryBtn);
    return;
  }

  const btn = e.target.closest(".edit-nick-btn");
  if (!btn) {
    if (!e.target.closest("a, button, input, form")) {
      const row = e.target.closest("tr.player-row");
      if (row && row.dataset.nick && state.currentScan) {
        const key = row.dataset.playerUid || row.dataset.nick;
        const nextRow = row.nextElementSibling;
        const hasDetailsRow = nextRow && nextRow.classList.contains("player-details-row");
        if (state.expandedNicks.has(key)) {
          state.expandedNicks.delete(key);
          row.classList.remove("expanded");
          if (hasDetailsRow) nextRow.remove();
        } else {
          state.expandedNicks.add(key);
          row.classList.add("expanded");
          if (!hasDetailsRow) {
            const player = (state.currentScan.players || []).find((p) => playerKey(p) === key);
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
  const playerUid = btn.dataset.playerUid;
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
          ...(playerUid ? { player_uid: playerUid } : {}),
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

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => showMainTab(btn.dataset.tab));
});

document.getElementById("closeCompareBtn").addEventListener("click", () => document.getElementById("avatarCompareDialog").close());
document.getElementById("closeCompareXBtn").addEventListener("click", () => document.getElementById("avatarCompareDialog").close());

els.logsContainer.addEventListener("click", (e) => {
  const cmp = e.target.closest(".compare-cand-btn");
  if (cmp) {
    openAvatarCompare(cmp.dataset.nick, cmp.dataset.steamid64);
    return;
  }
  const ed = e.target.closest(".open-nick-editor-btn");
  if (ed) openNickEditor(ed.dataset.nick);
});

els.logsContainer.addEventListener("click", async (e) => {
  const btn = e.target.closest(".pick-cand-btn");
  if (!btn || !state.currentScan) return;
  btn.disabled = true;
  const oldNick = btn.dataset.oldNick;
  const playerUid = btn.dataset.playerUid;
  const url = btn.dataset.url;
  setStatus(`Оновлюю профіль для "${oldNick}"...`);
  try {
    const res = await fetch("/api/recheck", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_id: state.currentScan.id, old_nick: oldNick, ...(playerUid ? { player_uid: playerUid } : {}), query: url }),
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
  state.riskFilter = "high-critical";
  state.onlyWithData = false;
  state.onlyHighRisk = false;
  els.searchInput.value = "";
  els.dateFilter.value = "";
  els.riskFilter.value = "high-critical";
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

document.getElementById("closeSetupXBtn")?.addEventListener("click", () => els.cs2SetupDialog.close());
els.closeSetupBtn.addEventListener("click", () => {
  els.cs2SetupDialog.close();
});

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

els.closeSettingsXBtn?.addEventListener("click", () => els.settingsDialog.close());
els.cancelSettingsBtn?.addEventListener("click", () => els.settingsDialog.close());

els.helpBtn?.addEventListener("click", () => {
  if (els.toolsMenu) els.toolsMenu.open = false;
  els.helpDialog.showModal();
});
els.closeHelpBtn?.addEventListener("click", () => els.helpDialog.close());
els.closeHelpXBtn?.addEventListener("click", () => els.helpDialog.close());

loadLobbyPrefs();

document.getElementById("rateLimitRescanBtn")?.addEventListener("click", () => els.rescanBtn.click());

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (document.querySelector("dialog[open]")) return;
  const target = e.target;
  // Не перехоплюємо клавіші, коли користувач друкує у полі вводу.
  if (target?.closest?.("input, textarea, select, [contenteditable='true']")) return;
  if (target?.isContentEditable) return;

  if (e.key === "?") {
    e.preventDefault();
    setStatus(SHORTCUT_HINT);
    return;
  }
  if (e.key === "j" || e.key === "k") {
    if (!els.playersBody.querySelector("tr.player-row")) return;
    e.preventDefault();
    moveRowFocus(e.key === "j" ? 1 : -1);
    return;
  }
  if (e.key === "Enter") {
    // Enter на кнопці/посиланні має спрацьовувати нативно, не подвійно з рядком.
    const row = focusedPlayerRow();
    if (!row || target.closest("button, a")) return;
    e.preventDefault();
    row.click();
    return;
  }
  if (e.key === "a") {
    const row = focusedPlayerRow();
    if (!row) {
      setStatus("Спочатку виберіть рядок клавішами j / k.", true);
      return;
    }
    const aiBtn = row.querySelector(".run-ai-player-btn");
    if (!aiBtn || aiBtn.disabled) {
      setStatus("AI-аналіз для цього гравця зараз недоступний (профіль ще завантажується).", true);
      return;
    }
    e.preventDefault();
    aiBtn.click();
  }
});

fetchLatest();
connectSSE();
