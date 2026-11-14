"""Freezes config_store behaviour (load_config / save_config / get_public_config) over a matrix of inputs.

Runs against a temp CONFIG_FILE with fake keys only and a fixed sys.platform. Key ORDER is part of the
frozen output (it is the order the API returns and config.json is written in).

Regenerate ONLY after an intentional change:  python tests/test_config_parity.py --regen
"""

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import storage.config_store as config_store  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "config_golden.json"
ENV_KEYS = ("STEAM_API_KEY", "FACEIT_API_KEY", "ANTHROPIC_API_KEY")
K1 = "sk-TESTKEY-1234567890"
K2 = "sk-TESTKEY-ABCDEFGHIJ"
ENVK = "env-TESTKEY-ZYXWVUTSRQ"

FULL_FILE = {
    "tab_delay_sec": 1.25, "tab_cooldown_sec": 6.0, "cs2_only": False, "cs2_game_dir": "/games/cs2",
    "console_log_path": "/logs/console.log", "screenshots_dir": "/shots", "screenshot_tool": "grim",
    "notify_enabled": False, "steam_api_key": K1, "faceit_api_key": K2, "ai_enabled": True,
    "ai_provider": "anthropic_api", "ai_model": "opus", "ai_prompt": "custom prompt",
    "ai_weights": {"scoreboard": 0.2, "leetify": 0.5, "steam_account": 0.3},
    "anthropic_api_key": K1, "leetify_api_key": "lf-TESTKEY-1234567890", "leetify_cache_ttl_sec": 600,
    "unknown_extra": "dropped",
}


def _s(**kw):
    return kw


def _scenarios():
    bad_numbers = {
        "tab_delay_sec": "abc", "tab_cooldown_sec": None, "cs2_only": None, "notify_enabled": "yes",
        "ai_enabled": "0", "leetify_cache_ttl_sec": "12",
    }
    bad_enums = {"screenshot_tool": " GRIM ", "ai_provider": "OpenAI", "ai_model": "   ",
                 "cs2_game_dir": 0, "console_log_path": False, "screenshots_dir": 5}
    return [
        _s(name="absent_file_no_env", file=None, env={}, steps=[("load",), ("public", {})]),
        _s(name="absent_file_env_all_set", file=None,
           env={"STEAM_API_KEY": ENVK, "FACEIT_API_KEY": ENVK, "ANTHROPIC_API_KEY": ENVK},
           steps=[("load",), ("public", {})]),
        _s(name="absent_file_env_param_only", file=None, env={},
           steps=[("public", {"env_key_present": True, "env_faceit_key_present": True})]),
        _s(name="invalid_json_file", file="{not json", env={}, steps=[("load",), ("public", {})]),
        _s(name="json_list_file", file="[1, 2, 3]", env={}, steps=[("load",), ("public", {})]),
        _s(name="full_valid_file", file=FULL_FILE, env={}, steps=[("load",), ("public", {"resolved": {"x": 1}})]),
        _s(name="bad_numbers_file", file=bad_numbers, env={}, steps=[("load",)]),
        _s(name="bad_enums_text_file", file=bad_enums, env={}, steps=[("load",)]),
        _s(name="weights_partial_and_invalid", file={"ai_weights": {"scoreboard": 2, "leetify": "x"}},
           env={}, steps=[("load",)]),
        _s(name="weights_not_dict", file={"ai_weights": [1, 2]}, env={}, steps=[("load",)]),
        _s(name="ttd_edge_values", file={"leetify_cache_ttl_sec": 3.9}, env={}, steps=[("load",)]),
        _s(name="ttd_negative_and_huge", file={"leetify_cache_ttl_sec": -5}, env={}, steps=[("load",)]),
        _s(name="ttd_huge_int", file={"leetify_cache_ttl_sec": 999999}, env={}, steps=[("load",)]),
        _s(name="ttd_true", file={"leetify_cache_ttl_sec": True}, env={}, steps=[("load",)]),
        _s(name="float_nan_inf_strings", file={"tab_delay_sec": "nan", "tab_cooldown_sec": "inf"}, env={},
           steps=[("load",)]),
        _s(name="ttd_infinity_literal_file", file="{\"leetify_cache_ttl_sec\": Infinity}", env={},
           steps=[("load",)]),
        _s(name="keep_on_empty_steam", file={"steam_api_key": K1}, env={},
           steps=[("save", {"steam_api_key": ""}), ("public", {})]),
        _s(name="replace_steam_stripped", file={"steam_api_key": K1}, env={},
           steps=[("save", {"steam_api_key": f"  {K2}  "}), ("public", {})]),
        _s(name="clear_steam_flag", file={"steam_api_key": K1}, env={},
           steps=[("save", {"clear_steam_api_key": True}), ("public", {})]),
        _s(name="clear_steam_flag_with_new_value", file={"steam_api_key": K1}, env={},
           steps=[("save", {"clear_steam_api_key": True, "steam_api_key": K2}), ("public", {})]),
        _s(name="faceit_set_keep_clear", file=None, env={},
           steps=[("save", {"faceit_api_key": K2}), ("save", {"faceit_api_key": None}),
                  ("save", {"clear_faceit_api_key": True, "faceit_api_key": K1}), ("public", {})]),
        _s(name="anthropic_set_keep_clear", file=None, env={},
           steps=[("save", {"anthropic_api_key": K1}), ("save", {"anthropic_api_key": "   "}),
                  ("save", {"clear_anthropic_api_key": True}), ("public", {})]),
        _s(name="leetify_set_empty_clears_and_flag_ignored", file=None, env={},
           steps=[("save", {"leetify_api_key": "lf-TESTKEY-1234567890"}),
                  ("save", {"clear_leetify_api_key": True}), ("public", {}),
                  ("save", {"leetify_api_key": ""}), ("public", {})]),
        _s(name="unknown_and_non_config_keys_ignored", file=None, env={},
           steps=[("save", {"not_a_key": 1, "clear_cs2_only": True, "tab_delay_sec": 2.0}), ("load",)]),
        _s(name="none_values_for_normal_keys", file=None, env={},
           steps=[("save", {"tab_delay_sec": None, "cs2_only": None, "ai_prompt": None,
                            "screenshot_tool": None, "leetify_cache_ttl_sec": None}), ("load",)]),
        _s(name="falsy_values_text_and_secret_rules", file=None, env={},
           steps=[("save", {"cs2_game_dir": 0, "console_log_path": False, "anthropic_api_key": 0,
                            "steam_api_key": 0, "leetify_api_key": False}), ("public", {})]),
        _s(name="truthy_non_string_values", file=None, env={},
           steps=[("save", {"cs2_game_dir": 5, "anthropic_api_key": 5, "steam_api_key": 5,
                            "ai_model": 7, "ai_prompt": 9}), ("load",)]),
        _s(name="ai_prompt_blank_variants", file=None, env={},
           steps=[("save", {"ai_prompt": "   "}), ("load",), ("save", {"ai_prompt": ""}), ("load",)]),
        _s(name="ai_weights_save_partial", file=None, env={},
           steps=[("save", {"ai_weights": {"scoreboard": 0.9}}), ("load",)]),
        _s(name="env_and_file_key_precedence", file={"steam_api_key": K1, "faceit_api_key": K2}, env={
            "STEAM_API_KEY": ENVK, "FACEIT_API_KEY": ENVK, "ANTHROPIC_API_KEY": ENVK},
           steps=[("public", {"env_key_present": True})]),
        _s(name="masked_format_lengths", file=None, env={},
           steps=[("save", {"steam_api_key": "abc"}), ("public", {}),
                  ("save", {"steam_api_key": "123456"}), ("public", {}),
                  ("save", {"steam_api_key": "1234567"}), ("public", {}),
                  ("save", {"steam_api_key": "sk-TESTKEY-LONGERVALUE-9999"}), ("public", {})]),
        _s(name="multi_step_save_load_public", file=None, env={},
           steps=[("save", {"ai_enabled": True, "ai_provider": "claude_cli", "steam_api_key": K1}),
                  ("load",), ("save", {"screenshot_tool": "mss"}), ("public", {"resolved": {"cs2_game_dir": ""}}),
                  ("save", {"notify_enabled": 0}), ("load",)]),
        _s(name="save_invalid_existing_file_recovers", file="{broken", env={},
           steps=[("save", {"cs2_only": False}), ("load",)]),
        _s(name="save_infinity_ttl_raises", file=None, env={},
           steps=[("save", {"leetify_cache_ttl_sec": float("inf")}), ("load",)]),
    ]


def _jsonable(obj):
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        if math.isinf(obj):
            return "Infinity" if obj > 0 else "-Infinity"
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _run_scenario(sc: dict) -> list:
    tmp = tempfile.TemporaryDirectory()
    try:
        cfg_path = Path(tmp.name) / "config.json"
        if sc["file"] is not None:
            text = sc["file"] if isinstance(sc["file"], str) else json.dumps(sc["file"])
            cfg_path.write_text(text, encoding="utf-8")
        env_clean = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
        env_clean.update(sc["env"])
        out = []
        with mock.patch.object(config_store, "CONFIG_FILE", cfg_path), \
                mock.patch.dict(os.environ, env_clean, clear=True), \
                mock.patch.object(config_store.sys, "platform", "linux"):
            for step in sc["steps"]:
                op = step[0]
                rec = {"op": op}
                if op == "load":
                    rec["result"] = config_store.load_config()
                elif op == "public":
                    kwargs = step[1] if len(step) > 1 else {}
                    rec["args"] = kwargs
                    rec["result"] = config_store.get_public_config(**kwargs)
                elif op == "save":
                    rec["args"] = step[1]
                    try:
                        rec["result"] = config_store.save_config(dict(step[1]))
                    except Exception as exc:  # frozen: which exception type escapes
                        rec["raised"] = type(exc).__name__
                    rec["file_after"] = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else None
                out.append(_jsonable(rec))
        return out
    finally:
        tmp.cleanup()


def _compute() -> dict:
    return {sc["name"]: _run_scenario(sc) for sc in _scenarios()}


def _canon(obj) -> str:
    # Order-sensitive canonical form: key order is part of the contract.
    return json.dumps(obj, ensure_ascii=False)


def _regen() -> None:
    data = _compute()
    GOLDEN.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN} ({len(data)} scenarios)")


class ConfigParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_scenario_set_matches_golden(self):
        self.assertEqual([s["name"] for s in _scenarios()], list(self.golden))

    def test_every_scenario_matches_frozen_output_including_key_order(self):
        actual = _compute()
        for name, golden_steps in self.golden.items():
            with self.subTest(scenario=name):
                self.assertEqual(_canon(actual[name]), _canon(golden_steps),
                                 f"config behaviour changed for scenario {name!r}")

    def test_fixtures_use_only_fake_keys(self):
        text = GOLDEN.read_text(encoding="utf-8")
        for real_looking in ("sk-", "lf-"):
            # Only the TESTKEY-marked fake values may appear.
            for token in text.split('"'):
                if token.startswith(real_looking):
                    self.assertIn("TESTKEY", token, token)


if __name__ == "__main__":
    if "--regen" in sys.argv:
        _regen()
        sys.exit(0)
    unittest.main()
