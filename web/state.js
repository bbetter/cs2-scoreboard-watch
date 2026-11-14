export const state = {
  currentScan: null,
  matches: [],
  history: [],
  sortKey: "cheat_probability",
  sortDir: "descending",
  searchQuery: "",
  dateFilter: "",
  matchFilter: "",
  riskFilter: "high-critical",
  onlyWithData: false,
  onlyHighRisk: false,
  expandedNicks: new Set(),
  playerTabs: new Map(),
};

// Element refs are resolved on first access, so importing this module needs no DOM (tests import it in Node).
export const els = lazyRefs({
  sseStatus: () => document.getElementById("sseStatus"),
  sseLabel: () => document.getElementById("sseLabel"),
  scanNowBtn: () => document.getElementById("scanNowBtn"),
  rescanBtn: () => document.getElementById("rescanBtn"),
  fileInput: () => document.getElementById("fileInput"),
  dropZone: () => document.getElementById("dropZone"),
  searchInput: () => document.getElementById("searchInput"),
  dateFilter: () => document.getElementById("dateFilter"),
  matchSelect: () => document.getElementById("matchSelect"),
  riskFilter: () => document.getElementById("riskFilter"),
  lobbyRisk: () => document.getElementById("lobbyRisk"),
  lobbySources: () => document.getElementById("lobbySources"),
  onlyWithDataFilter: () => document.getElementById("onlyWithDataFilter"),
  onlyHighRiskFilter: () => document.getElementById("onlyHighRiskFilter"),
  clearFiltersBtn: () => document.getElementById("clearFiltersBtn"),
  setupCs2Btn: () => document.getElementById("setupCs2Btn"),
  aiAnalyzeScanBtn: () => document.getElementById("aiAnalyzeScanBtn"),
  toolsMenu: () => document.getElementById("toolsMenu"),
  settingsBtn: () => document.getElementById("settingsBtn"),
  helpBtn: () => document.getElementById("helpBtn"),
  cs2SetupDialog: () => document.getElementById("cs2SetupDialog"),
  settingsDialog: () => document.getElementById("settingsDialog"),
  settingsForm: () => document.getElementById("settingsForm"),
  closeSettingsXBtn: () => document.getElementById("closeSettingsXBtn"),
  cancelSettingsBtn: () => document.getElementById("cancelSettingsBtn"),
  saveSettingsBtn: () => document.getElementById("saveSettingsBtn"),
  settingsSaveMsg: () => document.getElementById("settingsSaveMsg"),
  cfgTabDelay: () => document.getElementById("cfgTabDelay"),
  cfgTabCooldown: () => document.getElementById("cfgTabCooldown"),
  cfgCs2Only: () => document.getElementById("cfgCs2Only"),
  cfgNotifyEnabled: () => document.getElementById("cfgNotifyEnabled"),
  cfgCs2Dir: () => document.getElementById("cfgCs2Dir"),
  cfgCs2DirHint: () => document.getElementById("cfgCs2DirHint"),
  cfgConsoleLog: () => document.getElementById("cfgConsoleLog"),
  cfgConsoleLogHint: () => document.getElementById("cfgConsoleLogHint"),
  cfgScreenshotsDir: () => document.getElementById("cfgScreenshotsDir"),
  cfgScreenshotsDirHint: () => document.getElementById("cfgScreenshotsDirHint"),
  resetPathsBtn: () => document.getElementById("resetPathsBtn"),
  cfgScreenshotTool: () => document.getElementById("cfgScreenshotTool"),
  cfgSteamApiKey: () => document.getElementById("cfgSteamApiKey"),
  cfgSteamKeyStatus: () => document.getElementById("cfgSteamKeyStatus"),
  clearSteamKeyBtn: () => document.getElementById("clearSteamKeyBtn"),
  cfgFaceitApiKey: () => document.getElementById("cfgFaceitApiKey"),
  cfgFaceitKeyStatus: () => document.getElementById("cfgFaceitKeyStatus"),
  clearFaceitKeyBtn: () => document.getElementById("clearFaceitKeyBtn"),
  cfgAiEnabled: () => document.getElementById("cfgAiEnabled"),
  cfgAiProvider: () => document.getElementById("cfgAiProvider"),
  cfgAiModel: () => document.getElementById("cfgAiModel"),
  cfgWeightScoreboard: () => document.getElementById("cfgWeightScoreboard"),
  cfgWeightLeetify: () => document.getElementById("cfgWeightLeetify"),
  cfgWeightSteam: () => document.getElementById("cfgWeightSteam"),
  cfgAiPrompt: () => document.getElementById("cfgAiPrompt"),
  cfgAnthropicApiKey: () => document.getElementById("cfgAnthropicApiKey"),
  cfgAnthropicKeyStatus: () => document.getElementById("cfgAnthropicKeyStatus"),
  clearAnthropicKeyBtn: () => document.getElementById("clearAnthropicKeyBtn"),
  cfgClaudeCliStatus: () => document.getElementById("cfgClaudeCliStatus"),
  helpDialog: () => document.getElementById("helpDialog"),
  closeHelpBtn: () => document.getElementById("closeHelpBtn"),
  closeHelpXBtn: () => document.getElementById("closeHelpXBtn"),
  setupStatusText: () => document.getElementById("setupStatusText"),
  launchOptionsCode: () => document.getElementById("launchOptionsCode"),
  copyLaunchBtn: () => document.getElementById("copyLaunchBtn"),
  closeSetupBtn: () => document.getElementById("closeSetupBtn"),
  historySelect: () => document.getElementById("historySelect"),
  statusMsg: () => document.getElementById("statusMsg"),
  statPlayers: () => document.getElementById("statPlayers"),
  statSuspects: () => document.getElementById("statSuspects"),
  statSusLabel: () => document.getElementById("statSusLabel"),
  statTopSuspect: () => document.getElementById("statTopSuspect"),
  statLastScan: () => document.getElementById("statLastScan"),
  playersTable: () => document.getElementById("playersTable"),
  playersBody: () => document.getElementById("playersBody"),
  previewDetails: () => document.getElementById("previewDetails"),
  screenshotPreview: () => document.getElementById("screenshotPreview"),
  tabPanelTable: () => document.getElementById("tabPanelTable"),
  tabPanelLogs: () => document.getElementById("tabPanelLogs"),
  tabCountTable: () => document.getElementById("tabCountTable"),
  tabCountLogs: () => document.getElementById("tabCountLogs"),
  logsContainer: () => document.getElementById("logsContainer"),
});

function lazyRefs(getters) {
  const refs = {};
  for (const [key, get] of Object.entries(getters)) {
    Object.defineProperty(refs, key, {
      enumerable: true,
      configurable: true,
      get() {
        const value = get();
        Object.defineProperty(refs, key, { value, enumerable: true, configurable: true, writable: true });
        return value;
      },
    });
  }
  return refs;
}

export const RISK_LABELS = {
  low: "Низький",
  medium: "Середній",
  high: "Високий",
  critical: "Критичний",
};

export const LOBBY_PREFS_KEY = "cswatcher.lobbyFilters.v1";
export const RISK_FILTER_VALUES = ["all", "suspicious", "smurf", "critical", "banned", "high-critical"];

// Метрики лобі: витягують значення з запису гравця (p.steam, як у renderPlayerDetailsRow).
export const LOBBY_METRICS = {
  lf_rating: (p) => {
    const lf = p.steam?.leetify || {};
    return (lf.ranks || {}).leetify ?? lf.leetify_rating;
  },
  lf_aim: (p) => (p.steam?.leetify?.rating || {}).aim,
  reaction: (p) => (p.steam?.leetify?.stats || {}).reaction_time_ms,
  ct_trust: (p) => (p.steam?.extra_stats?.cstracker || {}).trust,
  ct_preaim: (p) => ((((p.steam?.extra_stats?.cstracker || {}).sections || {}).aim_reactions) || {}).Preaim,
};

export const SOURCE_STATE_LABELS = {
  loaded: "завантажено",
  none: "немає",
  blocked: "заблоковано",
  error: "помилка",
  rate: "ліміт запитів",
};

export const ATTENTION_AVATAR_MIN = 0.5;
export const ATTENTION_BETTER_GAP = 0.3;

// Гарячі клавіші таблиці: j/k — рядок вниз/вгору, Enter — розгорнути, a — AI для рядка, ? — підказка.
export const SHORTCUT_HINT =
  "Гарячі клавіші: j / k — рядок вниз / вгору · Enter — розгорнути / згорнути · a — AI-аналіз рядка · ? — ця підказка";

