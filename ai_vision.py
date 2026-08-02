"""Calorie analysis via a multimodal vision model through an OpenAI-compatible API.

Works with OpenAI-compatible proxies (e.g. a router that fronts Gemini). The model
sees the photo, estimates the dish, portion weight, and per-100g macros, then we
cross-check (optionally) against OpenFoodFacts.
"""

from __future__ import annotations

import base64
import json
import logging
import os

from dotenv import load_dotenv
from openai import OpenAI

# override=True: the machine may already have a global OPENAI_BASE_URL (e.g.
# deepseek); the .env value must win for this bot.
load_dotenv(override=True)

log = logging.getLogger("caloriebot.ai")

# The router's key is stored in GEMINI_API_KEY (kept as-is to avoid touching .env
# conventions); the base URL and model name come from dedicated variables.
KEY = os.getenv("GEMINI_API_KEY", "")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://routerai.ru/api/v1")
MODEL = os.getenv("GEMINI_MODEL", "google/gemini-3.1-flash-lite")

# Ask the model to reply with this exact JSON shape so we can parse it.
_PROMPT = """You are a nutrition expert. Look at the food in this photo and estimate
what it is and roughly how much there is (a normal home plate is about 200-300g of
cooked food).

Reply with ONLY valid JSON, no markdown, no extra text, in this exact schema:
{
  "dishes": [
    {
      "name": "human readable name in Russian",
      "grams": <estimated portion weight in grams, integer>,
      "kcal": <estimated calories for this portion, integer>,
      "protein_g": <grams of protein for this portion, number>,
      "fat_g": <grams of fat for this portion, number>,
      "carbs_g": <grams of carbs for this portion, number>,
      "confidence": <0.0 to 1.0 how sure you are about this dish>
    }
  ],
  "note": "<one short sentence in Russian, e.g. whether the portion looks big/small>"
}
Rules:
- If you cannot identify any food, return dishes as an empty array and put a short
  explanation in note.
- Prefer the most likely single interpretation, don't list every hypothesis.
- Round sensible values; an average serving should fall in a realistic range."""

_client_cache: OpenAI | None = None


def _client() -> OpenAI:
    global _client_cache
    if _client_cache is None:
        _client_cache = OpenAI(api_key=KEY, base_url=BASE_URL)
    return _client_cache


def analyze_photo(image_bytes: bytes) -> dict:
    """Send photo bytes to the vision model and return a structured nutrition dict."""
    if not KEY:
        raise RuntimeError("GEMINI_API_KEY is not set. Fill it in .env")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    response = _client().chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
    )

    text = response.choices[0].message.content or ""
    return _parse_json(text)


def _parse_json(text: str) -> dict:
    """Extract the first JSON object from the model's reply, tolerating fences."""
    text = text.strip()
    if text.startswith("```"):
        # strip ```json ... ```
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: cut at first '{' and last '}'.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Could not parse model output: {text[:300]!r}")


def escape_md(text: str) -> str:
    """Escape characters that break Telegram Markdown V1.

    Food names / notes come from the vision model and may contain '*', '_',
    or '`'; those would break the *bold* and _italic_ spans below.
    """
    return str(text).replace("\\", "\\\\").replace("*", "\\*") \
        .replace("_", "\\_").replace("`", "\\`")


# Backwards-compatible alias used internally.
_escape_md = escape_md


def summarize(data: dict) -> str:
    """Turn the AI's structured dishes into a friendly Russian message."""
    dishes = data.get("dishes") or []
    if not dishes:
        return "😕 Не смог распознать еду на фото. " + data.get("note", "")

    total_kcal = round(sum(float(d.get("kcal", 0)) for d in dishes))
    total_p = round(sum(float(d.get("protein_g", 0)) for d in dishes))
    total_f = round(sum(float(d.get("fat_g", 0)) for d in dishes))
    total_c = round(sum(float(d.get("carbs_g", 0)) for d in dishes))

    lines = ["🍽 *Оценка приёма пищи:*", ""]
    for d in dishes:
        name = _escape_md(d.get("name", "?"))
        grams = round(float(d.get("grams", 0)))
        kcal = round(float(d.get("kcal", 0)))
        conf = float(d.get("confidence", 0))
        lines.append(
            f"• *{name}* — ~{grams} г, {kcal} ккал "
            f"(доверие {round(conf * 100)}%)"
        )
    lines += [
        "",
        f"🧮 *Итого:* {total_kcal} ккал "
        f"(белки {total_p} г, жиры {total_f} г, углеводы {total_c} г)",
        "",
        f"_{_escape_md(data.get('note', ''))}_",
        "",
        "_Это оценка по фото, а не точный подсчёт._",
    ]
    return "\n".join(lines)
