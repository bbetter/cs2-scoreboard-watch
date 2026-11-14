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
  clearFiltersBtn: document.getElementById("clearFiltersBtn"),
  setupCs2Btn: document.getElementById("setupCs2Btn"),
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

function applyPlayerFilters(players) {
  const q = state.searchQuery.trim().toLowerCase();
  return players.filter((p) => {
    const steam = p.steam || {};
    if (state.riskFilter === "suspicious" && p.cheat_probability < 55) return false;
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
  const suspects = players.filter((p) => p.cheat_probability >= 55);
  const top = [...players].sort((a, b) => b.cheat_probability - a.cheat_probability)[0];

  els.statPlayers.textContent = String(players.length);
  els.statSuspects.textContent = String(suspects.length);
  els.statTopSuspect.textContent = top ? `${top.nick} (${top.cheat_probability}%)` : "—";
  els.statLastScan.textContent = `${scan.created_at.replace("T", " ")} (${scan.duration_ms} мс)`;

  if (scan.image_url) {
    els.previewDetails.hidden = false;
    els.screenshotPreview.src = `${scan.image_url}?t=${Date.now()}`;
  } else {
    els.previewDetails.hidden = true;
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

      const profileName = steam.profile_name
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

      const lf = steam.leetify;
      let leetifyLine = "";
      if (lf) {
        const parts = [];
        if (lf.premier) parts.push(`Premier ${lf.premier}`);
        if (lf.faceit) parts.push(`FACEIT ${lf.faceit}`);
        if (lf.winrate != null) parts.push(`WR ${lf.winrate}%`);
        if (lf.aim != null) parts.push(`Aim ${lf.aim}`);
        if (lf.reaction_time_ms != null) parts.push(`RT ${lf.reaction_time_ms}ms`);
        if (parts.length) {
          leetifyLine = `<span class="steam-sub">${escapeHtml(parts.join(" · "))}</span>`;
        }
      }

      const riskLabel = RISK_LABELS[p.risk_level] || p.risk_level;
      const reasons = (p.reasons || []).slice(0, 3).map(escapeHtml).join(" · ");
      const ripUrl = steam.rip_url || `https://steamcommunity.rip/id/${encodeURIComponent(p.nick)}/`;
      const steamLink = steam.profile_url
        ? `<a class="link-btn" href="${escapeHtml(steam.profile_url)}" target="_blank" rel="noopener noreferrer">Steam</a>`
        : "";
      const leetifyLink = lf?.url
        ? `<a class="link-btn" href="${escapeHtml(lf.url)}" target="_blank" rel="noopener noreferrer">Leetify</a>`
        : "";

      return `
        <tr>
          <td data-label="Гравець">
            <div class="player-cell">
              ${avatarHtml}
              <div class="player-names">
                <div class="nick-row">
                  <span class="nick-main">${escapeHtml(p.nick)}</span>
                  <button type="button" class="edit-nick-btn" data-nick="${escapeHtml(p.nick)}" title="Виправити нік або вставити Steam URL / SteamID64">✎</button>
                </div>
                <span class="steam-sub">${profileName}</span>
                ${leetifyLine}
                ${tags.length ? `<div class="badge-row">${tags.join("")}</div>` : ""}
              </div>
            </div>
          </td>
          <td data-label="K">${p.kills}</td>
          <td data-label="D">${p.deaths}</td>
          <td data-label="K/D">${Number(p.kd).toFixed(2)}</td>
          <td data-label="HS%">${Number(p.hs).toFixed(0)}%</td>
          <td data-label="Lvl">${p.level >= 0 ? p.level : "?"}</td>
          <td data-label="Ймовірність" class="prob-cell">
            <div class="prob-top">
              <span class="prob-num">${p.cheat_probability}%</span>
              <span class="risk-pill risk-${escapeHtml(p.risk_level)}">${escapeHtml(riskLabel)}</span>
            </div>
            <div class="prob-bar" aria-hidden="true">
              <div class="prob-fill fill-${escapeHtml(p.risk_level)}" style="width: ${Math.min(100, Math.max(0, p.cheat_probability))}%"></div>
            </div>
            <div class="reasons-text">${reasons}</div>
          </td>
          <td data-label="Посилання">
            <div class="links-cell">
              ${leetifyLink}
              <a class="link-btn link-btn-rip" href="${escapeHtml(ripUrl)}" target="_blank" rel="noopener noreferrer">rip</a>
              ${steamLink}
            </div>
          </td>
        </tr>`;
    })
    .join("");
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
    fetchLatest();
    setStatus(`Новий скан по шорткату: ${scan.player_count} гравців.`);
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

els.playersBody.addEventListener("click", (e) => {
  const btn = e.target.closest(".edit-nick-btn");
  if (!btn || !state.currentScan) return;
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
  els.searchInput.value = "";
  els.dateFilter.value = "";
  els.riskFilter.value = "all";
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

function populateSettingsDialog(data) {
  const cfg = data.config || {};
  const resolved = data.resolved || {};
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

fetchLatest();
connectSSE();
