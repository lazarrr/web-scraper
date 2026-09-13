"""
context_generator.py
~~~~~~~~~~~~~~~~~~~~
Generates a short context summary and 1-2 questions for an ingested page
using a local Ollama model.  The result enriches each URL entry in
crawl_history.json.

Usage
-----
    gen = ContextGenerator(model="qwen2.5:7b-instruct-q4_K_M")
    result = gen.generate(url, text, title="...")
    # -> {"context": "...", "questions": ["...", "..."]} or None
"""

from __future__ import annotations

import json

import requests


_PROMPT_TEMPLATE = """Ti si asistent koji indeksira sadržaj univerzitetskog veb sajta za RAG sistem.

URL stranice: {url}
Naslov: {title}

Sadržaj stranice:
\"\"\"
{text}
\"\"\"

Tvoj zadatak:
1. Napiši kratak kontekst (2-3 rečenice) koji opisuje o čemu je stranica. Piši na srpskom jeziku.
2. Napiši 1-2 pitanja na srpskom jeziku na koja se može odgovoriti isključivo iz sadržaja stranice.

Vrati isključivo JSON objekat, bez ikakvog dodatnog teksta:
{{"context": "...", "questions": ["...", "..."]}}"""


class ContextGenerator:
    """Thin client for the Ollama HTTP API used to summarize pages and
    derive questions they can answer."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b-instruct-q4_K_M",
        timeout: int = 120,
        max_input_chars: int = 6000,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_input_chars = max_input_chars

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def generate(
        self, url: str, text: str, title: str | None = None
    ) -> dict | None:
        """
        Generate {"context": str, "questions": list[str]} for a page.

        Returns None when Ollama is unreachable or the response cannot
        be parsed.
        """
        snippet = " ".join(text.split())[: self.max_input_chars]
        if not snippet:
            return None

        prompt = _PROMPT_TEMPLATE.format(
            url=url,
            title=title or url,
            text=snippet,
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_predict": 512},
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
        except (requests.RequestException, ValueError) as exc:
            print(f"  Warning: Ollama context generation failed: {exc}")
            return None

        return self._parse(raw)

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse(raw: str) -> dict | None:
        """Parse the model output, tolerating markdown code fences."""
        raw = (raw or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end <= start:
                print("  Warning: Ollama returned no JSON object")
                return None
            try:
                data = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                print("  Warning: could not parse Ollama JSON response")
                return None

        context = data.get("context")
        if not isinstance(context, str) or not context.strip():
            context = None

        questions = data.get("questions") or []
        if isinstance(questions, str):
            questions = [questions]
        questions = [
            q.strip() for q in questions
            if isinstance(q, str) and q.strip()
        ][:2]

        if context is None and not questions:
            return None
        return {"context": context, "questions": questions}
