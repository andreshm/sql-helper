from __future__ import annotations


class GeminiClient:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def ask(self, prompt: str) -> str:
        if not self.api_key:
            return "[Gemini API key not configured. Add it to config.yaml or set GEMINI_API_KEY.]"
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel(self.model)
            response = model.generate_content(prompt)
            return response.text.strip()
        except ImportError:
            return "[google-generativeai package not installed. Run: pip install google-generativeai]"
        except Exception as exc:
            return f"[Gemini error: {exc}]"
