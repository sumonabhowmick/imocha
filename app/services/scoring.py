"""Pure scoring helpers: percentage and performance categories.

Categories are computed deterministically here, never by the AI model. They describe
a score on one assessment, not a person's overall professional competence.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def validate_category_scale(scale: list[dict]) -> None:
    """A scale must cover 0-100 completely with contiguous, non-overlapping bands.

    Each band is {"label", "min", "max"}; min is inclusive and max exclusive, except
    the last band, which also includes 100.
    """
    if not scale:
        raise ValueError("The category scale needs at least one band.")
    bands = sorted(scale, key=lambda b: b["min"])
    labels = [b["label"].strip() for b in bands]
    if any(not label for label in labels) or len(set(labels)) != len(labels):
        raise ValueError("Category labels must be non-empty and unique.")
    if bands[0]["min"] != 0:
        raise ValueError("The first category must start at 0%.")
    if bands[-1]["max"] != 100:
        raise ValueError("The last category must end at 100%.")
    for band in bands:
        if band["max"] <= band["min"]:
            raise ValueError(f"Category '{band['label']}' must have max greater than min.")
    for previous, current in zip(bands, bands[1:]):
        if current["min"] != previous["max"]:
            raise ValueError(
                f"Categories '{previous['label']}' and '{current['label']}' leave a gap or overlap."
            )


def calculate_percentage(raw_score: Decimal, max_score: Decimal) -> Decimal | None:
    """(earned / possible) x 100, rounded to 2 places. None when nothing is possible to score."""
    if max_score <= 0:
        return None
    pct = (Decimal(raw_score) / Decimal(max_score)) * Decimal("100")
    return pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def categorize(percentage: Decimal | float | None, scale: list[dict]) -> str | None:
    """Return the band label for a percentage, or None when the score is unavailable/invalid."""
    if percentage is None:
        return None
    pct = Decimal(str(percentage))
    if pct < 0 or pct > 100:
        return None
    bands = sorted(scale, key=lambda b: b["min"])
    for i, band in enumerate(bands):
        is_last = i == len(bands) - 1
        lower = Decimal(str(band["min"]))
        upper = Decimal(str(band["max"]))
        if lower <= pct < upper or (is_last and pct == upper):
            return band["label"]
    return None
