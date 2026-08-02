"""Cross-check dish macros against the free OpenFoodFacts database.

The vision model guesses kcal/protein/fat/carbs from the photo. OpenFoodFacts
holds real per-100g macros for thousands of foods, so we look up the recognised
dish name and can refine the estimate (or at least show a real-food reference).

API (free, no key): https://world.openfoodfacts.org/api/v2/
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger("caloriebot.nufacts")

_EXTRA_HEADERS = {"User-Agent": "calorie_bot/1.0 (personal Telegram bot)"}


def lookup(name: str, grams: int) -> dict | None:
    """Search OpenFoodFacts for a dish name and return real per-portion macros.

    Returns a dict with name/kcal/protein_g/fat_g/carbs_g scaled to ``grams``,
    or None if nothing usable was found.
    """
    query = _normalise(name)
    if not query:
        return None

    url = "https://world.openfoodfacts.org/cgi/search.pl"
    params = {
        "search_terms": query,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 5,
        "fields": "product_name,nutriments,kcal_value,energy-kcal_100g",
    }

    try:
        resp = requests.get(url, params=params, headers=_EXTRA_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log.warning("OpenFoodFacts request failed for %r: %s", name, exc)
        return None

    products = data.get("products") or []
    for p in products:
        macro = _extract(p, grams)
        if macro is not None:
            return macro
    return None


def _extract(product: dict, grams: int) -> dict | None:
    """Scale a product's per-100g macros to the portion size."""
    nut = product.get("nutriments") or {}
    kcal100 = _to_float(nut.get("energy-kcal_100g") or nut.get("energy_100g"))
    kcals = (kcal100 / 100) * grams if kcal100 is not None else None
    p100 = _to_float(nut.get("proteins_100g"))
    f100 = _to_float(nut.get("fat_100g"))
    c100 = _to_float(nut.get("carbohydrates_100g"))

    if kcals is None:
        return None

    return {
        "name": product.get("product_name") or "?",
        "kcal": round(kcals),
        "protein_g": round((p100 or 0) / 100 * grams),
        "fat_g": round((f100 or 0) / 100 * grams),
        "carbs_g": round((c100 or 0) / 100 * grams),
    }


def _normalise(name: str) -> str:
    """Clean a dish name: drop parentheticals and weird chars, keep latin."""
    import re

    cleaned = re.sub(r"\([^)]*\)", "", str(name))
    cleaned = re.sub(r"[^a-zA-Zа-яА-Я0-9 ]+", " ", cleaned)
    return cleaned.strip()


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
