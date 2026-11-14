import { renderScan, updateMatchAndHistorySelects } from "/scan-table.js";
import { els, state } from "/state.js";
import { setStatus } from "/util.js";

export async function fetchLatest() {
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

export async function triggerScan() {
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

export async function uploadFile(file) {
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

export function connectSSE() {
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

// Повтор одного зовнішнього джерела (Leetify / CSTracker) для одного гравця.
export async function retrySource(btn) {
  const { nick, source } = btn.dataset;
  const playerUid = btn.dataset.playerUid;
  const sourceLabel = source === "leetify" ? "Leetify" : "CSTracker";
  btn.disabled = true;
  const prevText = btn.textContent;
  btn.textContent = "Повторюю...";
  setStatus(`Повторно запитую ${sourceLabel} для ${nick}...`);
  try {
    const res = await fetch("/api/recheck-source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scan_id: state.currentScan.id, nick, source, ...(playerUid ? { player_uid: playerUid } : {}) }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    renderScan(data);
    setStatus(`${sourceLabel} для ${nick} оновлено.`);
  } catch (err) {
    setStatus(`Помилка повтору (${sourceLabel}): ${err.message}`, true);
    btn.disabled = false;
    btn.textContent = prevText;
  }
}

