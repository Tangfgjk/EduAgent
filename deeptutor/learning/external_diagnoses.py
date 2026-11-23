"""Owner/workspace-scoped preview, commit, revoke and advisory projections.

No external model execution and no imported result grants local mastery.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import time

from deeptutor.learning.external_diagnosis_models import (
    ExternalCommitRequest,
    ExternalDiagnosisRecord,
)

# A product recency policy, not a validated retention estimate.
REFERENCE_MAX_AGE_DAYS = 90
MAX_STORED_IMPORTS = 100


class ExternalImportConflict(ValueError):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def outline_digest(progress):
    return digest(
        [
            {"id": p.id, "name": p.name, "type": p.type.value, "prerequisites": p.prerequisite_ids}
            for m in progress.modules
            for p in m.knowledge_points
        ]
    )


def _import_id(document):
    return digest(
        [
            document.source_system.casefold(),
            document.student_ref,
            document.result_id,
            document.result_version,
        ]
    )


def _preview(progress, request):
    doc = request.document
    points = {p.id: p for m in progress.modules for p in m.knowledge_points}
    entries = {e.knowledge_id: e for e in doc.estimates}
    supplied = {link.knowledge_id: link for link in request.links}
    if len(supplied) != len(request.links) or set(supplied) - set(entries):
        raise ValueError("Mappings must have distinct knowledge IDs present in the document")
    for link in request.links:
        if link.objective_id and link.objective_id not in points:
            raise ValueError("Mapped objective does not belong to this project's current outline")
    rows = []
    for entry in doc.estimates:
        link = supplied.get(entry.knowledge_id)
        untested = entry.measurement_status == "unmeasured" or (
            doc.zero_encoding == "zero_is_untested" and entry.probability == 0
        )
        rows.append(
            {
                **entry.model_dump(mode="json"),
                "measurement_status": "unmeasured" if untested else "estimated",
                "probability": None if untested else entry.probability,
                "objective_id": link.objective_id if link else "",
                "relation": link.relation if link else "unmapped",
                "suggestions": [
                    p.id
                    for p in points.values()
                    if p.name.strip().casefold() == entry.knowledge_name.strip().casefold()
                ],
            }
        )
    mappings = [
        {
            "knowledge_id": r["knowledge_id"],
            "objective_id": r["objective_id"],
            "relation": r["relation"],
        }
        for r in rows
    ]
    document_digest = digest(doc.model_dump(mode="json"))
    scope_digest = outline_digest(progress)
    preview_digest = digest([document_digest, scope_digest, mappings])
    mapped = sum(bool(r["objective_id"]) for r in rows)
    measured = sum(r["measurement_status"] == "estimated" for r in rows)
    today = datetime.now(timezone.utc).date()
    age = (today - doc.observed_on).days
    warnings = [
        "Self-uploaded result: student identity and provider authenticity have not been independently verified.",
        "Mappings require your confirmation. Same names do not guarantee the same scope.",
    ]
    if age > REFERENCE_MAX_AGE_DAYS:
        warnings.append(
            "Older than 90 days: retained as history, not an active teaching reference."
        )
    if mapped < len(rows) or len(rows) < doc.total_knowledge_count:
        warnings.append("Partial coverage: some report entries or project mappings are missing.")
    if any(r["relation"] == "partial" for r in rows):
        warnings.append("Partial mappings are limited references, not equivalent objectives.")
    prior = next((r for r in progress.external_diagnoses if r.import_id == _import_id(doc)), None)
    if prior and prior.preview_digest != preview_digest:
        raise ExternalImportConflict(
            "This result/version already exists with different data or mappings. Use a new result_version."
        )
    if prior and prior.revoked_at is not None:
        raise ExternalImportConflict(
            "This result/version was revoked; it will not reactivate automatically. Use a new result_version."
        )
    return {
        "import_id": _import_id(doc),
        "document_digest": document_digest,
        "outline_digest": scope_digest,
        "preview_digest": preview_digest,
        "source_revision": progress.version,
        "rows": rows,
        "coverage": {
            "declared": doc.total_knowledge_count,
            "supplied": len(rows),
            "measured": measured,
            "mapped": mapped,
            "unmapped": len(rows) - mapped,
            "mapped_measured": sum(
                bool(r["objective_id"]) and r["measurement_status"] == "estimated" for r in rows
            ),
        },
        "warnings": warnings,
        "can_import": mapped > 0,
        "duplicate": prior is not None,
    }


def preview_import(store, path_id, request):
    progress = store.load(path_id)
    if progress is None:
        raise LookupError("Learning project not found")
    return _preview(progress, request)


def commit_import(store, path_id, request: ExternalCommitRequest):
    if not request.belongs_to_me or not request.mapping_confirmed:
        raise ValueError(
            "Confirm that this report belongs to you and that you reviewed the mappings"
        )

    def save(tx):
        preview = _preview(tx.progress, request)
        if (
            request.outline_digest != preview["outline_digest"]
            or request.preview_digest != preview["preview_digest"]
        ):
            raise ExternalImportConflict(
                "The outline, document or mappings changed. Preview again before importing."
            )
        if not preview["can_import"]:
            raise ValueError("Confirm at least one project mapping before importing")
        # A binding is scoped to the current account/workspace project, never a global verified identity.
        if any(
            r.revoked_at is None
            and r.document.source_system.casefold() == request.document.source_system.casefold()
            and r.document.student_ref != request.document.student_ref
            for r in tx.progress.external_diagnoses
        ):
            raise ValueError(
                "This project is bound to another student reference for that source. Check the report identity."
            )
        prior = next(
            (r for r in tx.progress.external_diagnoses if r.import_id == preview["import_id"]), None
        )
        if prior:
            return {"import_id": prior.import_id, "duplicate": True}
        if len(tx.progress.external_diagnoses) >= MAX_STORED_IMPORTS:
            raise ValueError("This project has reached its diagnostic import limit")
        from deeptutor.learning.external_diagnosis_models import ExternalKnowledgeLink

        points = {p.id: p.name for m in tx.progress.modules for p in m.knowledge_points}
        record = ExternalDiagnosisRecord(
            import_id=preview["import_id"],
            document_digest=preview["document_digest"],
            preview_digest=preview["preview_digest"],
            outline_digest=preview["outline_digest"],
            document=request.document,
            links=[
                ExternalKnowledgeLink(
                    knowledge_id=r["knowledge_id"],
                    objective_id=r["objective_id"],
                    relation=r["relation"],
                )
                for r in preview["rows"]
            ],
            objective_names={
                r["objective_id"]: points[r["objective_id"]]
                for r in preview["rows"]
                if r["objective_id"]
            },
            imported_at=time.time(),
        )
        tx.progress.external_diagnoses.append(record)
        tx.emit(
            "diagnosis.external_imported",
            {
                "import_id": record.import_id,
                "coverage": preview["coverage"],
                "provenance": record.provenance,
            },
        )
        return {"import_id": record.import_id, "duplicate": False}

    progress, result = store.mutate(path_id, save)
    from deeptutor.learning.diagnostic_impact import import_impact

    return {**result, "impact": import_impact(progress, result["import_id"])}


def revoke_import(store, path_id, import_id):
    def revoke(tx):
        record = next((r for r in tx.progress.external_diagnoses if r.import_id == import_id), None)
        if record is None:
            raise LookupError("Imported diagnosis not found")
        if record.revoked_at is None:
            record.revoked_at = time.time()
            tx.emit("diagnosis.external_revoked", {"import_id": import_id})
        return {"import_id": import_id, "revoked": True}

    _, result = store.mutate(path_id, revoke)
    return result


def external_references(progress, objective_id=None):
    """Estimates remain separate from assessment observations; never averaged or scored."""
    points = {p.id: p.name for m in progress.modules for p in m.knowledge_points}
    today = datetime.now(timezone.utc).date()
    references = []
    for record in progress.external_diagnoses:
        if record.revoked_at is not None:
            continue
        entries = {e.knowledge_id: e for e in record.document.estimates}
        age = (today - record.document.observed_on).days
        for link in record.links:
            if not link.objective_id or objective_id and link.objective_id != objective_id:
                continue
            entry = entries[link.knowledge_id]
            measured = entry.measurement_status == "estimated" and not (
                record.document.zero_encoding == "zero_is_untested" and entry.probability == 0
            )
            scope_current = outline_digest(progress) == record.outline_digest and points.get(
                link.objective_id
            ) == record.objective_names.get(link.objective_id)
            references.append(
                {
                    "import_id": record.import_id,
                    "objective_id": link.objective_id,
                    "knowledge_name": entry.knowledge_name,
                    "relation": link.relation,
                    "probability": entry.probability if measured else None,
                    "measurement_status": "estimated" if measured else "unmeasured",
                    "source_system": record.document.source_system,
                    "model_version": record.document.model_version,
                    "taxonomy_version": record.document.taxonomy_version,
                    "mapping_version": record.document.mapping_version,
                    "result_id": record.document.result_id,
                    "result_version": record.document.result_version,
                    "subject": record.document.subject,
                    "scope": record.document.scope,
                    "observed_on": record.document.observed_on.isoformat(),
                    "provenance": record.provenance,
                    "stale": age > REFERENCE_MAX_AGE_DAYS,
                    "scope_current": scope_current,
                    "active_reference": measured
                    and scope_current
                    and age <= REFERENCE_MAX_AGE_DAYS,
                    "source_ref": "external_diagnoses:"
                    + record.import_id
                    + ":"
                    + entry.knowledge_id,
                }
            )
    return sorted(references, key=lambda r: r["observed_on"], reverse=True)


def list_imports(store, path_id):
    progress = store.load(path_id)
    if progress is None:
        raise LookupError("Learning project not found")
    return {
        "source_revision": progress.version,
        "imports": [r.model_dump(mode="json") for r in progress.external_diagnoses],
        "references": external_references(progress),
    }
