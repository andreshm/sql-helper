from __future__ import annotations
from app.ai.ollama_client import OllamaClient
from app.ai.anthropic_client import AnthropicClient
from app.ai.openai_client import OpenAIClient
from app.ai.gemini_client import GeminiClient


def get_ai_client(config: dict, override_model: str = ""):
    """Return the configured AI client based on config.yaml settings."""
    ai_cfg = config.get("ai", {})
    provider = ai_cfg.get("provider", "ollama").lower()

    if provider == "ollama":
        cfg = ai_cfg.get("ollama", {})
        model = override_model or cfg.get("model", "qwen2.5-coder:14b")
        return OllamaClient(
            base_url=cfg.get("base_url", "http://localhost:11434"),
            model=model,
        )
    elif provider == "anthropic":
        cfg = ai_cfg.get("anthropic", {})
        return AnthropicClient(api_key=cfg.get("api_key", ""), model=override_model or cfg.get("model", "claude-sonnet-4-6"))
    elif provider == "openai":
        cfg = ai_cfg.get("openai", {})
        return OpenAIClient(api_key=cfg.get("api_key", ""), model=override_model or cfg.get("model", "gpt-4o"))
    elif provider == "gemini":
        cfg = ai_cfg.get("gemini", {})
        return GeminiClient(api_key=cfg.get("api_key", ""), model=override_model or cfg.get("model", "gemini-1.5-pro"))
    else:
        raise ValueError(f"Unknown AI provider: {provider!r}. Choose: ollama, anthropic, openai, gemini")


def ask(config: dict, prompt: str, override_model: str = "") -> str:
    """Convenience wrapper — get client and ask in one call."""
    return get_ai_client(config, override_model=override_model).ask(prompt)


def check_ollama_status(base_url: str = "http://localhost:11434") -> tuple[bool, list[str]]:
    """Check if local Ollama is reachable and return list of installed models."""
    client = OllamaClient(base_url=base_url)
    online = client.is_online()
    models = client.list_models() if online else []
    return online, models
