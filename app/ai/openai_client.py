from __future__ import annotations


class OpenAIClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key)
            except ImportError:
                raise RuntimeError("openai package not installed. Run: pip install openai")
        return self._client

    def ask(self, prompt: str) -> str:
        if not self.api_key:
            return "[OpenAI API key not configured. Add it to config.yaml or set OPENAI_API_KEY.]"
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            return f"[OpenAI error: {exc}]"
