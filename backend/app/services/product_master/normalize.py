"""Product text normalization shared by master upload, matching and learning."""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")

MAX_DESCRIPTION_LENGTH = 255
# sales_records.product is VARCHAR(150); longer standard values are never applied.
MAX_APPLIED_PRODUCT_LENGTH = 150


def clean_display(value: object) -> str:
    """Trim and collapse whitespace while keeping the original wording/case."""
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    return _SPACES.sub(" ", text).strip()


def normalize_key(value: object) -> str:
    """Case/punctuation-insensitive matching key: ``"Ab-820 "`` -> ``"AB 820"``."""
    text = unicodedata.normalize("NFKC", str(value if value is not None else "")).upper()
    return _NON_ALNUM.sub(" ", text).strip()


def compact_key(value: object) -> str:
    """Key without separators: ``"AB 820"`` -> ``"AB820"``."""
    return normalize_key(value).replace(" ", "")


def digit_groups(value: object) -> list[str]:
    return _DIGITS.findall(normalize_key(value))


def role_value(current: object) -> str:
    role = getattr(current, "role", "")
    return str(getattr(role, "value", role) or "")
