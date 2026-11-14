import { LOBBY_METRICS, els, state } from "/state.js";

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function setStatus(text, isError = false) {
  els.statusMsg.textContent = text;
  els.statusMsg.style.color = isError ? "var(--critical)" : "var(--accent)";
}

export function formatDuration(sec) {
  if (!sec || sec < 60) return `${sec || 0} с`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m} хв ${s} с` : `${m} хв`;
}

export function hasDataObject(value) {
  return !!value && typeof value === "object" && Object.keys(value).length > 0;
}

export function detailKv(label, value) {
  if (value == null || value === "" || value === "—") return "";
  return `<div class="detail-kv"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

export function detailSection(title, rows) {
  const body = rows.filter(Boolean).join("");
  if (!body) return "";
  return `<div class="detail-section"><h5>${escapeHtml(title)}</h5><div class="detail-kv-list">${body}</div></div>`;
}

export function fmtNum(v, d = 1, suffix = "") {
  return v == null || Number.isNaN(Number(v)) ? null : `${Number(v).toFixed(d)}${suffix}`;
}

// --- U3: міні-графіки історії матчів і порівняння з медіаною лобі ---
export function toNum(v) {
  if (typeof v === "number") return v;
  if (typeof v === "string") return parseFloat(v);
  return NaN;
}

// Інлайн SVG-лінія. Повертає "" якщо значень немає, текст "мало даних" якщо < 2 точок.
export function renderSparkline(values, opts = {}) {
  const nums = (Array.isArray(values) ? values : []).map(toNum).filter(Number.isFinite);
  if (!nums.length) return "";
  if (nums.length < 2) return `<span class="spark-empty">мало даних</span>`;
  const w = opts.width || 120;
  const h = opts.height || 28;
  const pad = 3;
  const min = Math.min(...nums);
  const max = Math.max(...nums);
  const span = max - min;
  const stepX = (w - pad * 2) / (nums.length - 1);
  const yOf = (v) => (span === 0 ? h / 2 : pad + (1 - (v - min) / span) * (h - pad * 2));
  const pts = nums.map((v, i) => [pad + i * stepX, yOf(v)]);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [lx, ly] = pts[pts.length - 1];
  const fmt = opts.fmt || ((v) => v.toFixed(1));
  const title = `${opts.label ? `${opts.label}: ` : ""}останнє ${fmt(nums[nums.length - 1])}, мін ${fmt(min)}, макс ${fmt(max)} (${nums.length} матчів)`;
  return `<svg class="sparkline" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="${escapeHtml(title)}"><title>${escapeHtml(title)}</title><polyline fill="none" stroke="currentColor" stroke-width="1.5" points="${line}"/><circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="2.5" fill="currentColor"/></svg>`;
}

export function detailSparkRow(label, values, opts = {}) {
  const svg = renderSparkline(values, { ...opts, label });
  if (!svg) return "";
  return `<div class="detail-kv detail-kv-spark"><span>${escapeHtml(label)}</span>${svg}</div>`;
}

// Медіана по поточному скану; null якщо в лобі < 3 гравців з даними.
export function lobbyMedian(key) {
  const getter = LOBBY_METRICS[key];
  if (!getter) return null;
  const vals = (state.currentScan?.players || []).map((p) => toNum(getter(p))).filter(Number.isFinite);
  if (vals.length < 3) return null;
  vals.sort((a, b) => a - b);
  const mid = vals.length >> 1;
  const med = vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2;
  return { med, n: vals.length };
}

// Стрілка і відсоток відхилення значення гравця від медіани лобі; "" якщо вибірка замала.
export function lobbyTag(key, raw) {
  const v = toNum(raw);
  const m = Number.isFinite(v) ? lobbyMedian(key) : null;
  if (!m || m.med === 0) return "";
  const pct = ((v - m.med) / Math.abs(m.med)) * 100;
  const arrow = Math.abs(pct) < 0.5 ? "≈" : pct > 0 ? "▲" : "▼";
  const sign = pct > 0 ? "+" : "";
  return ` ${arrow} ${sign}${pct.toFixed(0)}% від лобі`;
}

export function withLobbyTag(text, key, raw) {
  return text == null ? null : text + lobbyTag(key, raw);
}

export function emptyTabText(text) {
  return `<p class="player-tab-empty">${escapeHtml(text)}</p>`;
}

export function wrapTabCard(inner) {
  return `<div class="detail-card">${inner || emptyTabText("Даних для цього розділу немає.")}</div>`;
}


// Stable key for a player row: the scan-unique player_uid when the record has one, else the nick (legacy).
export function playerKey(p) {
  return p.player_uid || p.nick;
}

// Extra attribute carrying the uid into the DOM. Empty for legacy records, so their markup is unchanged.
export function uidAttr(p) {
  return p.player_uid ? ` data-player-uid="${escapeHtml(p.player_uid)}"` : "";
}
