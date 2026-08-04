"""Central normalization rules used for deterministic context resolution."""

import re
import unicodedata


def normalize_reference(value: str) -> str:
    """Normalize names and aliases for exact, punctuation-insensitive matching."""
    folded = unicodedata.normalize("NFKC", value).casefold()
    tokens = re.sub(r"[^\w]+", " ", folded).split()
    normalized: list[str] = []
    initials: list[str] = []
    for token in tokens + [""]:
        if len(token) == 1 and token.isalnum():
            initials.append(token)
            continue
        if initials:
            normalized.append("".join(initials) if len(initials) > 1 else initials[0])
            initials = []
        if token:
            normalized.append(token)
    return " ".join(normalized)


def normalize_key(value: str) -> str:
    """Normalize predicates and relationship names without inventing semantics."""
    return "_".join(re.sub(r"[^\w]+", " ", value.casefold()).split())
