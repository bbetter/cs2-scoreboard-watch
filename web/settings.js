import { els } from "/state.js";
import { setStatus } from "/util.js";

export let pendingClearSteamKey = false;
export let pendingClearFaceitKey = false;
export let pendingClearAnthropicKey = false;

export function populateSettingsDialog(data) {
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

