import os
import yaml
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        return _default_config()
    with open(_CONFIG_PATH, "r") as f:
        data = yaml.safe_load(f) or {}
    cfg = _default_config()
    _deep_merge(cfg, data)
    _apply_env_overrides(cfg)
    return cfg


def _default_config() -> dict:
    return {
        "ai": {
            "provider": "ollama",
            "ollama": {"base_url": "http://localhost:11434", "model": "sqlcoder:7b"},
            "anthropic": {"api_key": "", "model": "claude-sonnet-4-6"},
            "openai": {"api_key": "", "model": "gpt-4o"},
            "gemini": {"api_key": "", "model": "gemini-1.5-pro"},
        },
        "connections": [],
    }


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _apply_env_overrides(cfg: dict) -> None:
    ai = cfg["ai"]
    if os.getenv("ANTHROPIC_API_KEY"):
        ai["anthropic"]["api_key"] = os.getenv("ANTHROPIC_API_KEY")
    if os.getenv("OPENAI_API_KEY"):
        ai["openai"]["api_key"] = os.getenv("OPENAI_API_KEY")
    if os.getenv("GEMINI_API_KEY"):
        ai["gemini"]["api_key"] = os.getenv("GEMINI_API_KEY")
    if os.getenv("OLLAMA_BASE_URL"):
        ai["ollama"]["base_url"] = os.getenv("OLLAMA_BASE_URL")
