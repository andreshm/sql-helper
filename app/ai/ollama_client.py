from __future__ import annotations
import requests


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", model: str = "qwen2.5-coder:14b"):
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.model = model or "qwen2.5-coder:14b"

    def ask(self, prompt: str) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.exceptions.ConnectionError:
            return (
                f"[⚠️ Ollama is not reachable at {self.base_url}. "
                "Ensure Ollama is running (`ollama serve`).]"
            )
        except Exception as exc:
            return f"[⚠️ Ollama Error: {exc}]"

    def is_online(self) -> bool:
        """Quick health check to see if Ollama server is running."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Fetch all downloaded model tags from local Ollama."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=3)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return models
            return []
        except Exception:
            return []
