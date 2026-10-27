"""Deterministic diagnosis over already-audited evidence.

These are pure functions: they take tool results the agent loop has already
fetched and recorded, and return scores and decisions. The loop owns the tool
calls, persistence, and phase transitions.
"""

from __future__ import annotations

from . import evidence, knowledge

KEYWORD_SIGNAL_STRENGTH = 0.6  # fixed contribution of a keyword hit (untrusted channel)
CASE_PRECEDENT_BOOST = 1.0  # a validated-success precedent naming the cause
NEGATIVE_PRECEDENT_PENALTY = 0.5  # a validated-failure precedent on the same asset
CONTRADICTION_WEIGHT = 2.0  # trusted evidence that argues against a failure mode


def untrusted_texts(cases, work_orders, inspections, manuals) -> list[tuple[str, str]]:
    """(ref, text) pairs for every free-text item, in a fixed order."""
    items: list[tuple[str, str]] = []
    for wo in work_orders.result["open_work_orders"]:
        items.append((wo["notes"]["ref"], wo["notes"]["content"]))
    for insp in inspections.result["inspections"]:
        items.append((insp["notes"]["ref"], insp["notes"]["content"]))
    for chunk in manuals.result["chunks"]:
        items.append((chunk["content"]["ref"], chunk["content"]["content"]))
    for case in cases.result["matches"]:
        items.append((case["narrative"]["ref"], case["narrative"]["content"]))
    return items


def merge_evidence_signals(
    signals: dict[str, float],
    observations: list[dict],
    texts: list[tuple[str, str]],
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Add independent observations and whitelisted text keywords to the
    trusted sensor signals.

    Returns the merged signals and, per signal, the evidence refs it came
    from. Free text can only contribute signals named in the keyword
    whitelist, at a fixed strength.
    """
    merged = dict(signals)
    sources: dict[str, list[str]] = {}
    for observation in observations:
        for sig, strength in observation["signal"].items():
            merged[sig] = max(merged.get(sig, 0.0), float(strength))
            sources[sig] = [observation["ref"]]
    for ref, text in texts:
        for sig in evidence.extract_signals(text, knowledge.KEYWORD_SIGNALS):
            merged[sig] = max(merged.get(sig, 0.0), KEYWORD_SIGNAL_STRENGTH)
            sources.setdefault(sig, []).append(ref)
    return merged, sources


def score_failure_modes(
    asset_type: str,
    asset_id: int,
    signals: dict[str, float],
    sources: dict[str, list[str]],
    case_matches: list[dict],
) -> list[dict]:
    """Rank the known failure modes for an asset type, best first.

    Each hypothesis carries its score, normalised confidence, the recommended
    action, and the evidence that contributed to it.

    Case memory: only validated-success precedents may boost a mode. A
    validated failure on this same asset counts against it. Provisional,
    rejected, or superseded entries are shown elsewhere but never scored.
    """
    hypotheses = []
    for mode in knowledge.failure_modes_for(asset_type):
        score = 0.0
        contributing: list[dict] = []
        for sig, weight in mode["signals"].items():
            strength = signals.get(sig, 0.0)
            if strength > 0:
                score += weight * strength
                contributing.append(
                    {
                        "signal": sig,
                        "strength": strength,
                        "weight": weight,
                        "sources": sources.get(sig, ["sensor_stats"]),
                    }
                )
        for sig in mode.get("contradicted_by", []):
            strength = signals.get(sig, 0.0)
            if strength > 0:
                score = max(0.0, score - CONTRADICTION_WEIGHT * strength)
                contributing.append(
                    {
                        "signal": sig,
                        "strength": strength,
                        "weight": -CONTRADICTION_WEIGHT,
                        "sources": sources.get(sig, ["sensor_stats"]),
                        "contradicting": True,
                    }
                )
        for case in case_matches:
            names_this_cause = case["root_cause"].lower() in mode["cause"].lower()
            if not names_this_cause or case["symptom_overlap"] < 2:
                continue
            if case.get("validation_status") == "validated_success":
                score += CASE_PRECEDENT_BOOST
                contributing.append(
                    {
                        "signal": "case_precedent",
                        "strength": 1.0,
                        "weight": CASE_PRECEDENT_BOOST,
                        "sources": [f"case_memory:{case['case_id']}"],
                    }
                )
            elif (
                case.get("validation_status") == "validated_failure"
                and case.get("asset_id") == asset_id
            ):
                score = max(0.0, score - NEGATIVE_PRECEDENT_PENALTY)
                contributing.append(
                    {
                        "signal": "negative_precedent",
                        "strength": 1.0,
                        "weight": -NEGATIVE_PRECEDENT_PENALTY,
                        "sources": [f"case_memory:{case['case_id']}"],
                    }
                )
        hypotheses.append(
            {
                "cause": mode["cause"],
                "score": round(score, 3),
                "action": mode["action"],
                "evidence": contributing,
            }
        )
    hypotheses.sort(key=lambda h: -h["score"])
    total = sum(h["score"] for h in hypotheses) or 1.0
    for h in hypotheses:
        h["confidence"] = round(h["score"] / total, 3)
    return hypotheses


def abstention(
    asset_type: str, asset_name: str, hypotheses: list[dict]
) -> tuple[str | None, list[str]]:
    """Decide whether the evidence is too weak or too ambiguous to act on.

    Returns (reason, evidence that would help), or (None, []) when the top
    hypothesis is clear enough to plan maintenance for.
    """
    if not hypotheses:
        return (
            f"unsupported asset type '{asset_type}' — no failure-mode "
            f"knowledge is configured for it",
            [
                f"human inspection of {asset_name} by a qualified technician",
                f"failure-mode signature definitions for asset type '{asset_type}'",
            ],
        )
    top = hypotheses[0]
    if top["score"] < knowledge.MIN_EVIDENCE_SCORE:
        return (
            f"insufficient evidence: top hypothesis '{top['cause']}' scored "
            f"{top['score']:.2f} < required {knowledge.MIN_EVIDENCE_SCORE}",
            [
                f"manual inspection of {asset_name}",
                "extended sensor history / additional telemetry",
                "recent oil analysis or equivalent condition data",
            ],
        )
    if len(hypotheses) > 1 and hypotheses[1]["score"] >= knowledge.TIE_RATIO * top["score"]:
        runner_up = hypotheses[1]
        return (
            f"ambiguous evidence: '{top['cause']}' ({top['score']:.2f}) and "
            f"'{runner_up['cause']}' ({runner_up['score']:.2f}) are materially tied "
            f"(ratio >= {knowledge.TIE_RATIO})",
            [
                f"discriminating diagnostic test between '{top['cause']}' "
                f"and '{runner_up['cause']}'",
                f"human inspection of {asset_name}",
            ],
        )
    return None, []


def discriminating_test(
    asset_type: str, hypotheses: list[dict], tests_already_run: list[dict]
) -> str | None:
    """An allow-listed independent test that separates the top two causes,
    if one exists and hasn't been run yet."""
    if len(hypotheses) < 2:
        return None
    pair = {hypotheses[0]["cause"], hypotheses[1]["cause"]}
    for test_id, spec in knowledge.tests_for(asset_type).items():
        already = any(t["test_id"] == test_id for t in tests_already_run)
        if not already and pair <= set(spec["discriminates"]):
            return test_id
    return None


def apply_provider_ranking(hypotheses: list[dict], ranked) -> list[dict]:
    """Reorder deterministic hypotheses to follow a validated provider ranking.

    Only causes already in the deterministic list can appear; anything the
    provider left out keeps its original relative order at the end. Where the
    provider's entry lines up with ours, its confidence and citations are
    attached.
    """
    by_cause = {h["cause"]: h for h in hypotheses}
    reranked = [by_cause[h.cause_id] for h in ranked if h.cause_id in by_cause]
    reranked += [h for h in hypotheses if h not in reranked]
    for det, prov in zip(reranked, ranked, strict=False):
        if det["cause"] == prov.cause_id:
            det["confidence"] = prov.confidence
            det["provider_citations"] = {
                "supporting": prov.supporting_refs,
                "contradicting": prov.contradicting_refs,
            }
    return reranked
