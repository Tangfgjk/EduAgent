"""Visible import impact: provenance, coverage, an optional next action and local follow-up.

This is a read-only explanation, not a new grade or a causal effect estimate.
"""

from __future__ import annotations

from deeptutor.learning.diagnosis import project_facts
from deeptutor.learning.external_diagnoses import external_references


def import_impact(progress, import_id=None):
    records = progress.external_diagnoses
    record = (
        next((r for r in records if r.import_id == import_id), None)
        if import_id
        else max(records, key=lambda r: r.imported_at, default=None)
    )
    if record is None:
        return None
    refs = [r for r in external_references(progress) if r["import_id"] == record.import_id]
    points = {p.id: p for m in progress.modules for p in m.knowledge_points}
    measured = sum(
        e.measurement_status == "estimated"
        and not (record.document.zero_encoding == "zero_is_untested" and e.probability == 0)
        for e in record.document.estimates
    )
    from datetime import datetime, timezone

    from deeptutor.learning.external_diagnoses import REFERENCE_MAX_AGE_DAYS, outline_digest

    stale = (
        datetime.now(timezone.utc).date() - record.document.observed_on
    ).days > REFERENCE_MAX_AGE_DAYS
    current = outline_digest(progress) == record.outline_digest
    status = (
        "revoked"
        if record.revoked_at is not None
        else "scope_changed"
        if not current
        else "historical"
        if stale
        else "active"
    )
    facts = project_facts(progress)
    from deeptutor.learning.wenjin import objective_evidence

    coverage = {
        "declared": record.document.total_knowledge_count,
        "supplied": len(record.document.estimates),
        "measured": measured,
        "untested": len(record.document.estimates) - measured,
        "mapped": sum(bool(link.objective_id) for link in record.links),
        "unmapped": sum(not link.objective_id for link in record.links),
        "usable": sum(r["active_reference"] for r in refs),
    }
    candidates = []
    followups = []
    for ref in refs:
        point = points.get(ref["objective_id"])
        if point is None:
            continue
        evidence = objective_evidence(progress, point)
        new_rows = sorted(
            [
                o
                for o in facts.observations
                if o.objective_id == point.id
                and o.validity == "recorded"
                and o.occurred_at >= record.imported_at
                and not (
                    o.context
                    and o.context.objective_name
                    and o.context.objective_name != point.name
                )
            ],
            key=lambda o: o.occurred_at,
        )
        if new_rows:
            latest = new_rows[-1]
            followups.append(
                {
                    "objective_id": point.id,
                    "objective_name": point.name,
                    "observation_id": latest.observation_id,
                    "occurred_at": latest.occurred_at,
                    "result": latest.result,
                    "assistance": latest.assistance,
                    "ability": latest.ability,
                    "achieved": evidence["achieved"],
                    "missing": evidence["missing"],
                    "source_refs": latest.source_refs,
                    "new_observations": len(new_rows),
                    "interpretation": "local_observation_after_import_not_causal_effect",
                }
            )
        if ref["active_reference"] and evidence["missing"]:
            candidates.append(
                {
                    "objective_id": point.id,
                    "objective_name": point.name,
                    "knowledge_name": ref["knowledge_name"],
                    "probability": ref["probability"],
                    "relation": ref["relation"],
                    "missing": evidence["missing"],
                    "has_local_evidence": bool(evidence["attempts"]),
                    "source_ref": ref["source_ref"],
                }
            )
    # A low external estimate is a tentative starting-point suggestion, not a weakness label.
    candidates.sort(key=lambda r: (r["has_local_evidence"], r["probability"]))
    return {
        "import_id": record.import_id,
        "source_revision": progress.version,
        "status": status,
        "source_system": record.document.source_system,
        "observed_on": record.document.observed_on.isoformat(),
        "result_id": record.document.result_id,
        "provenance": record.provenance,
        "coverage": coverage,
        "recommendation": candidates[0] if status == "active" and candidates else None,
        "followups": sorted(
            {r["observation_id"]: r for r in followups}.values(),
            key=lambda r: r["occurred_at"],
            reverse=True,
        )[:5],
        "local_mastery_credit_from_import": False,
    }


def load_import_impact(store, path_id, import_id=None):
    progress = store.load(path_id)
    if progress is None:
        raise LookupError("Learning project not found")
    if import_id and not any(r.import_id == import_id for r in progress.external_diagnoses):
        raise LookupError("Imported diagnosis not found")
    return import_impact(progress, import_id)
