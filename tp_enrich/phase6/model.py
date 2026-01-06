"""
PHASE 6 MODEL — Token-based classification rules

Provides:
- train_from_examples(): Build token rules from labeled examples
- score_name(): Score a name against the trained model
"""
import re
from typing import List, Dict, Any, Tuple

_SUFFIXES = {
    "llc", "inc", "ltd", "co", "corp", "company", "gmbh", "srl", "plc", "lp", "llp", "pc", "pa",
    "studio", "studios", "shop", "store", "market", "markets", "foods", "food", "cafe", "bar", "golf",
    "roofing", "plumbing", "drain", "sewer", "logistics", "trucking", "transport", "construction",
    "detailing", "auto", "mobile", "fitness", "academy", "restaurant", "bistro", "bakery",
    "florist", "repair", "appliance", "computers", "computer", "software", "saas"
}

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_URLISH_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|io|co|us|ca|uk)\b", re.I)


def _tokens(name: str) -> List[str]:
    return [t.lower() for t in _WORD_RE.findall(name or "")][:30]


def train_from_examples(examples: List[Tuple[str, str]]) -> Dict[str, Any]:
    """
    Train token rules from labeled examples.

    Args:
        examples: List of (name, label) tuples where label is 'business' or 'person'

    Returns:
        Artifact dict with token_rules and metadata
    """
    biz_counts: Dict[str, int] = {}
    per_counts: Dict[str, int] = {}
    biz_n = 0
    per_n = 0

    for name, label in examples:
        if not name:
            continue
        toks = _tokens(name)
        if label == "business":
            biz_n += 1
            for t in toks:
                biz_counts[t] = biz_counts.get(t, 0) + 1
        elif label == "person":
            per_n += 1
            for t in toks:
                per_counts[t] = per_counts.get(t, 0) + 1

    scored = []
    for t, c in biz_counts.items():
        if len(t) <= 1:
            continue
        p = per_counts.get(t, 0)
        score = c - (p * 1.5)
        if t in _SUFFIXES:
            score += 8
        if score >= 2:
            scored.append((t, float(score)))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:120]

    return {
        "type": "phase6_token_rules_v1",
        "token_rules": [{"token": t, "weight": w} for t, w in top],
        "meta": {"biz_examples": biz_n, "person_examples": per_n},
    }


def score_name(name: str, artifact: Dict[str, Any]) -> Dict[str, Any]:
    """
    Score a name against trained token rules.

    Args:
        name: The name to classify
        artifact: Trained model artifact from train_from_examples()

    Returns:
        Dict with label, confidence, reasons, and score
    """
    n = (name or "").strip()
    if not n:
        return {"label": None, "confidence": 0.0, "reasons": ["blank_name"], "score": 0.0}

    toks = _tokens(n)
    rules = artifact.get("token_rules") or []
    wmap = {r["token"]: float(r.get("weight", 0.0)) for r in rules if r.get("token")}

    score = 0.0
    reasons = []

    # URL-like names are strong business signals
    if _URLISH_RE.search(n):
        score += 10.0
        reasons.append("urlish")

    # Count token hits
    hits = 0
    for t in toks:
        if t in wmap:
            score += wmap[t]
            hits += 1
    if hits:
        reasons.append(f"token_hits={hits}")

    # Check for business suffixes
    suffix_hits = [t for t in toks if t in _SUFFIXES]
    if suffix_hits:
        reasons.append(f"suffix={','.join(suffix_hits[:3])}")

    # Classification thresholds
    if score >= 12:
        return {"label": "business", "confidence": min(0.99, 0.60 + score / 40.0), "reasons": reasons, "score": score}
    if score <= 1.5:
        return {"label": "person", "confidence": 0.55, "reasons": reasons, "score": score}
    return {"label": None, "confidence": 0.0, "reasons": reasons, "score": score}
