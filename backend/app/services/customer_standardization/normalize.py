"""Customer name normalization: legal-form aware keys and token classes."""

from __future__ import annotations

import re
import unicodedata
from typing import FrozenSet, List, Tuple

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_SPACES = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")

MAX_CUSTOMER_NAME_LENGTH = 255

# Spelling variants of legal-entity words, canonicalized so that
# "ABC Pvt. Ltd." and "ABC PRIVATE LIMITED" share one key.
_LEGAL_CANON = {
    "PVT": "PRIVATE",
    "PRIVATE": "PRIVATE",
    "PRIV": "PRIVATE",
    "PRV": "PRIVATE",
    "LTD": "LIMITED",
    "LIMITED": "LIMITED",
    "LMT": "LIMITED",
    "CO": "COMPANY",
    "COMPANY": "COMPANY",
    "CORP": "CORPORATION",
    "CORPN": "CORPORATION",
    "CORPORATION": "CORPORATION",
    "INC": "INCORPORATED",
    "INCORPORATED": "INCORPORATED",
    "LLP": "LLP",
    "PLC": "PLC",
}
_GLUED_LEGAL = {
    "PVTLTD": ("PRIVATE", "LIMITED"),
    "PLTD": ("PRIVATE", "LIMITED"),
    "PVTLIMITED": ("PRIVATE", "LIMITED"),
}
LEGAL_TOKENS: FrozenSet[str] = frozenset(_LEGAL_CANON.values())

# Words that describe a business but never identify one on their own. Two names
# that only share these words are never treated as the same customer.
GENERIC_TOKENS: FrozenSet[str] = frozenset(
    {
        "AND", "OF", "THE", "FOR",
        "RUBBER", "RUBBERS", "INDUSTRIES", "INDUSTRY", "INDUSTRIAL", "INDS",
        "POLYMER", "POLYMERS", "CHEMICAL", "CHEMICALS", "CHEM", "CHEMIE",
        "PRODUCT", "PRODUCTS", "ENTERPRISE", "ENTERPRISES", "ENTERPRIZES",
        "TRADER", "TRADERS", "TRADING", "TRADE", "AGENCY", "AGENCIES",
        "INTERNATIONAL", "INTL", "INDIA", "INDIAN", "MILL", "MILLS",
        "MANUFACTURING", "MANUFACTURERS", "MFG", "PLASTIC", "PLASTICS",
        "PAPER", "PAPERS", "PACKAGING", "PACKAGERS", "TEXTILE", "TEXTILES",
        "FABRICS", "OVERSEAS", "EXPORT", "EXPORTS", "IMPORTS", "SALES",
        "SERVICES", "SOLUTIONS", "TECHNOLOGIES", "TECHNOLOGY", "TECH",
        "GROUP", "WORKS", "GLOBAL", "COATINGS", "PAINTS", "ADHESIVES",
        "FOOTWEAR", "LATEX", "UNIT", "PLANT", "DIVISION", "BROTHERS", "BROS",
        "SONS", "ASSOCIATES", "CORPORATE", "SYNTHETICS", "ELASTOMERS",
    }
)


def clean_display(value: object) -> str:
    """Trim and collapse whitespace while keeping the original wording/case."""
    text = unicodedata.normalize("NFKC", str(value if value is not None else ""))
    return _SPACES.sub(" ", text).strip()


def _raw_tokens(value: object) -> List[str]:
    text = unicodedata.normalize("NFKC", str(value if value is not None else "")).upper()
    text = text.replace("&", " AND ")
    return [t for t in _NON_ALNUM.sub(" ", text).split() if t]


def key_tokens(value: object) -> List[str]:
    """Canonical tokens: legal words unified, leading M/S and THE removed."""
    tokens = _raw_tokens(value)
    if tokens[:2] == ["M", "S"]:
        tokens = tokens[2:]
    elif tokens[:1] in (["MS"], ["MESSRS"]) and len(tokens) > 1:
        tokens = tokens[1:]
    if tokens[:1] == ["THE"] and len(tokens) > 1:
        tokens = tokens[1:]
    out: List[str] = []
    for i, tok in enumerate(tokens):
        if tok in _GLUED_LEGAL:
            out.extend(_GLUED_LEGAL[tok])
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok == "P" and _LEGAL_CANON.get(nxt) == "LIMITED":
            out.append("PRIVATE")
            continue
        if tok == "CO" and nxt in ("OP", "OPERATIVE", "OPERATIVES"):
            out.append(tok)
            continue
        out.append(_LEGAL_CANON.get(tok, tok))
    return out


def customer_key(value: object) -> str:
    """Space separated canonical key: ``"M/s Abc Pvt. Ltd."`` -> ``"ABC PRIVATE LIMITED"``."""
    return " ".join(key_tokens(value))


def mapping_key(value: object) -> str:
    """Registry key without separators (``"ABCPRIVATELIMITED"``); max 255 chars."""
    return customer_key(value).replace(" ", "")[:MAX_CUSTOMER_NAME_LENGTH]


def legal_form(tokens: List[str]) -> Tuple[str, ...]:
    return tuple(t for t in tokens if t in LEGAL_TOKENS)


def distinctive_tokens(tokens: List[str]) -> List[str]:
    return [t for t in tokens if t not in GENERIC_TOKENS and t not in LEGAL_TOKENS]


def digit_groups(tokens: List[str]) -> List[str]:
    return [d for t in tokens for d in _DIGITS.findall(t)]


def role_value(current: object) -> str:
    role = getattr(current, "role", "")
    return str(getattr(role, "value", role) or "")
