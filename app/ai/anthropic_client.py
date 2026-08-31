from __future__ import annotations


class AnthropicClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        return self._client

    def ask(self, prompt: str) -> str:
        if not self.api_key:
            return "[Anthropic API key not configured. Add it to config.yaml or set ANTHROPIC_API_KEY.]"
        try:
            client = self._get_client()
            message = client.messages.create(
                model=self.model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text.strip()
        except Exception as exc:
            return f"[Anthropic error: {exc}]"
