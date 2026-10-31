"""Collapse near-duplicate names (typos, casing) onto one canonical spelling."""
from collections import Counter
from difflib import SequenceMatcher

SIMILARITY_THRESHOLD = 0.9


def _norm(name: str) -> str:
    return " ".join(name.lower().split())


def cluster_names(names: list[str], threshold: float = SIMILARITY_THRESHOLD) -> dict[str, str]:
    """Map every name to a canonical spelling.

    The most frequent spelling in a cluster wins, so a one-off typo maps onto the
    spelling the rest of the data uses regardless of row order. Empty names are
    left out.
    """
    counts = Counter(n for n in names if n.strip())
    canonical: list[str] = []
    mapping: dict[str, str] = {}
    for name, _ in counts.most_common():
        match = next(
            (c for c in canonical if SequenceMatcher(None, _norm(name), _norm(c)).ratio() >= threshold),
            None,
        )
        if match is None:
            canonical.append(name)
            match = name
        mapping[name] = match
    return mapping
