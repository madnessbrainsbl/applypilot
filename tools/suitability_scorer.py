"""Zero-token deterministic candidate-job suitability scorer.
Inspired by CareerOps scoring pipeline: evaluates job postings using weighted
domain rules, keyword analysis, and negative constraint filters without calling external LLM APIs.
Zero budget ($0), zero tokens, microsecond execution.
"""
import json
import re
from pathlib import Path
from typing import Dict, Any, Tuple

_DISQUALIFIERS = [
    (re.compile(r"\b(?:active\s+)?(?:top\s+secret|ts/sci|secret\s+clearance|security\s+clearance\s+required)\b", re.I), -60, "security_clearance_required"),
    (re.compile(r"\b(?:u\.?s\.?\s+citizenship\s+required|must\s+be\s+a\s+u\.?s\.?\s+citizen)\b", re.I), -50, "us_citizenship_required"),
    (re.compile(r"\b(?:no\s+(?:visa\s+)?sponsorship\s+(?:provided|available)?|unable\s+to\s+sponsor)\b", re.I), -30, "no_sponsorship"),
    (re.compile(r"\b(?:must\s+reside\s+in\s+(?:australia|japan|brazil|india|singapore))\b", re.I), -40, "strict_unsupported_region"),
]

ROOT = Path(__file__).resolve().parents[1]
_CFG_CACHE: Dict[str, Any] = {}
_AUTOPILOT_CFG: Dict[str, Any] = {}
_PERSONA_ALIAS = {"security": "appsec"}  # old callers passed "security"


def _load_cfg() -> Dict[str, Any]:
    if not _CFG_CACHE:
        _CFG_CACHE.update(json.loads((ROOT / "autopilot" / "config.json").read_text(encoding="utf-8")))
    return _CFG_CACHE


def _terms(terms, text: str) -> list:
    """Config terms found in text as whole words (same \b rule as autopilot.title_fit)."""
    return [t for t in terms if re.search(r"\b" + re.escape(str(t).lower()) + r"\b", text.lower())]


def persona_title_fit(title: str, cfg: Dict[str, Any] = None):
    """Persona whose config required terms match the title and that passes autopilot.title_fit
    (excluded seniority terms included), else None. The scanners' ingestion gate."""
    import autopilot as A
    if cfg is None:
        if "cfg" not in _AUTOPILOT_CFG:
            _AUTOPILOT_CFG["cfg"] = A.read_json(A.CFG)
        cfg = _AUTOPILOT_CFG["cfg"]
    persona = A.classify_persona(title, cfg)
    if persona and A.title_fit({"title": title, "persona": persona}, cfg):
        return persona
    return None


def score_vacancy(title: str, description: str = "", persona: str = "creative", cfg: Dict[str, Any] = None) -> Dict[str, Any]:
    """Deterministic suitability score (0-100) and grade, driven by the persona's
    config search.required_title_terms / excluded_title_terms / good_terms."""
    title_text = title or ""
    desc_text = (description or "")[:4000]
    persona_key = _PERSONA_ALIAS.get(persona, persona)
    pcfg = ((cfg or _load_cfg()).get("personas") or {}).get(persona_key)
    if pcfg is None:
        raise KeyError(f"unknown persona {persona!r}: no personas.{persona_key} in config")
    search = pcfg.get("search") or {}

    score = 15
    reasons = []
    disqualifications = []

    excluded = _terms(search.get("excluded_title_terms", []), title_text)
    if excluded:
        score -= 100
        disqualifications.append(f"excluded_title_term:{excluded[0]} (-100)")
    required = _terms(search.get("required_title_terms", []), title_text)
    if required and not excluded:
        weight = 45 + min(10, 5 * (len(required) - 1))
        score += weight
        reasons.append(f"required_title_match:{required[0]} (+{weight})")
    elif not required:
        disqualifications.append("no_required_title_term (0)")
    if re.search(r"\b(?:internship|intern|working\s+student|unpaid)\b", title_text, re.I):
        score -= 35
        disqualifications.append("internship_role (-35)")

    combined_text = f"{title_text}\n{desc_text}"
    for pattern, penalty, reason in _DISQUALIFIERS:
        if pattern.search(combined_text):
            score += penalty
            disqualifications.append(f"{reason} ({penalty})")

    good = _terms(search.get("good_terms", []), desc_text)
    desc_points = min(3 * len(good), 20)
    if desc_points:
        score += desc_points
        reasons.append(f"good_terms ({len(good)} hits, +{desc_points})")

    final_score = max(0, min(100, score))

    if final_score >= 85:
        grade = "A"
    elif final_score >= 70:
        grade = "B"
    elif final_score >= 55:
        grade = "C"
    elif final_score >= 40:
        grade = "D"
    else:
        grade = "F"

    eligible = (final_score >= 55) and not any(k in d for d in disqualifications for k in ("clearance", "citizen", "excluded_title_term"))

    return {
        "score": final_score,
        "grade": grade,
        "eligible": eligible,
        "persona": persona_key,
        "reasons": reasons,
        "disqualifications": disqualifications
    }


if __name__ == "__main__":
    test_cases = [
        ("Photographer", "Studio lighting, Capture One.", "creative"),
        ("Senior Photographer", "", "creative"),
        ("Product Designer", "Figma design systems.", "creative"),
        ("Security Engineer", "Python, OWASP.", "appsec"),
        ("Senior Security Engineer - AppSec", "Code review, Python, Rust, OWASP top 10.", "appsec"),
        ("Security Guard - Night Shift", "Monitor CCTV and physical doors.", "appsec"),
    ]
    for t, d, p in test_cases:
        res = score_vacancy(t, d, p)
        print(f"[{res['grade']}] Score: {res['score']:2d} | Eligible: {str(res['eligible']):5s} | {t[:40]}")
