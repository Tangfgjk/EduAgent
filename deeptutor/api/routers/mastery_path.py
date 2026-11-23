"""Guided Learning API Router."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import html
import json
import re
import time
from typing import Literal
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, StrictInt
from pydantic import ValidationError as PydanticValidationError

from deeptutor.learning import policy as learning_policy
from deeptutor.learning import prompts as learning_prompts
from deeptutor.learning.external_diagnosis_models import (
    ExternalCommitRequest,
    ExternalPreviewRequest,
)
from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
    MasteryInteraction,
    MasteryTopic,
    ReadingLearningRecords,
    ReviewStrategyTemplate,
    TopicMetadata,
    TopicSource,
    TopicSourceKind,
)
from deeptutor.learning.objective_relations import (
    ObjectiveRelationError,
    RelationRefs,
    normalize_refs,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.topic_generation import MAX_MODULE_LIMIT
from deeptutor.services.settings.interface_settings import get_response_language
from deeptutor.utils.json_parser import parse_json_response

router = APIRouter()
ws_router = APIRouter()

#: Signals that change what a topic screen shows without advancing the path's
#: revision, so they are forwarded even when the durable event tail is empty.
#: A conversation joining or leaving the topic changes its session list; a
#: deleted topic changes everything.
_SCREEN_ONLY_SIGNALS = frozenset({"session.bound", "session.released", "topic.deleted"})


def get_learning_service() -> LearningService:
    # Create a fresh store + service per request to avoid object-level race conditions.
    store = LearningStore()
    return LearningService(store)


def _turn_application_service():
    from deeptutor.app.container import get_application_container

    return get_application_container().turns


def _validate_book_id(book_id: str) -> None:
    """Reject empty or path-traversal-bearing book ids (shared by all endpoints)."""
    if not book_id or ".." in book_id or "/" in book_id or "\\" in book_id or ":" in book_id:
        raise HTTPException(status_code=400, detail="Invalid book_id")


def _parse_modules(body_modules: list[dict]) -> list[LearningModule]:
    """Parse raw module dicts into LearningModule objects (shared by init/replace)."""
    modules: list[LearningModule] = []
    for i, m in enumerate(body_modules):
        kps_data = m.get("knowledge_points", [])
        try:
            kps = [KnowledgePoint(**kp) for kp in kps_data]
        except PydanticValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid knowledge_point data in modules[{i}]: {exc.errors()}",
            ) from exc
        # Remove knowledge_points from m to avoid duplicate argument to LearningModule.
        m_clean = {k: v for k, v in m.items() if k != "knowledge_points"}
        try:
            modules.append(LearningModule(knowledge_points=kps, **m_clean))
        except PydanticValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid module data in modules[{i}]: {exc.errors()}",
            ) from exc
    return modules


def _validate_runnable_modules(modules: list[LearningModule], *, status_code: int = 400) -> None:
    if not modules:
        raise HTTPException(
            status_code=status_code, detail="At least one learning module is required"
        )
    for mod in modules:
        if not mod.knowledge_points:
            raise HTTPException(
                status_code=status_code,
                detail=f"Module {mod.id!r} must contain at least one knowledge point",
            )


async def _cancel_active_learning_turn(book_id: str) -> None:
    learning_store = LearningStore()
    turns = _turn_application_service()
    lease = await asyncio.to_thread(learning_store.get_path_lease, book_id)
    if lease is not None:
        if lease.session_id == "__path_api__":
            # Another administrative mutation owns the path. The caller's
            # acquisition attempt will return a deterministic HTTP 409.
            return
        if await turns.cancel_turn_and_wait(lease.turn_id):
            # ``cancel_turn`` can finalize a restart orphan without an
            # in-memory task, so its normal runtime ``finally`` cannot release
            # the mastery path lease.
            await asyncio.to_thread(
                learning_store.release_path_lease,
                book_id,
                turn_id=lease.turn_id,
            )
        else:
            raise HTTPException(
                status_code=409,
                detail="Active learning turn could not be stopped before changing this path",
            )
        return

    # Compatibility for turns started before explicit path leases existed.
    session_ids = await asyncio.to_thread(learning_store.list_session_ids, book_id)
    if book_id not in session_ids:
        session_ids.append(book_id)
    from deeptutor.services.session import get_session_store

    session_store = get_session_store()
    for session_id in session_ids:
        for turn in await session_store.list_active_turns(session_id):
            if str(turn.get("capability") or "") == "mastery_path":
                if not await turns.cancel_turn_and_wait(turn["id"]):
                    raise HTTPException(
                        status_code=409,
                        detail="Active learning turn could not be stopped before changing this path",
                    )


@asynccontextmanager
async def _exclusive_path_mutation(book_id: str):
    """Cancel the tutor, then exclude a newly racing tutor/API write."""
    from deeptutor.learning.storage import PathLeaseConflictError

    await _cancel_active_learning_turn(book_id)
    store = LearningStore()
    operation_id = f"api-{uuid.uuid4().hex}"
    try:
        await asyncio.to_thread(
            store.acquire_path_lease,
            book_id,
            "__path_api__",
            operation_id,
            bind_session=False,
        )
    except PathLeaseConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Mastery path changed activity while the operation was starting; "
                f"active session: {exc.lease.session_id}"
            ),
        ) from exc
    try:
        yield
    finally:
        await asyncio.to_thread(
            store.release_path_lease,
            book_id,
            turn_id=operation_id,
        )


# ── Request models ───────────────────────────────────────────────────────────


class KnowledgePointInput(BaseModel):
    id: str | None = None
    client_ref: str = ""
    name: str = Field(..., min_length=1, max_length=200)
    type: KnowledgeType = KnowledgeType.CONCEPT
    module_id: str = ""
    prerequisite_ids: list[str] = Field(default_factory=list)
    prerequisite_refs: list[str] = Field(default_factory=list)
    topic_source_ids: list[str] = Field(default_factory=list)
    topic_source_refs: list[str] = Field(default_factory=list)


class ModuleInput(BaseModel):
    id: str | None = None
    name: str = Field(..., min_length=1, max_length=200)
    order: int | None = None
    objective: str = Field(default="", max_length=300)
    pass_threshold: float = 0.7
    knowledge_points: list[KnowledgePointInput] = Field(default_factory=list)


class InitModulesRequest(BaseModel):
    modules: list[ModuleInput]


class RenamePathRequest(BaseModel):
    """An empty name is a valid request: it restores the derived display name."""

    name: str = ""


class ReviewSettingsRequest(BaseModel):
    """A per-path recall target; higher values schedule shorter intervals."""

    desired_retention: float = Field(..., ge=0.7, le=0.99, allow_inf_nan=False)


class ReviewStrategyRequest(BaseModel):
    """Create/edit a custom review strategy: per-round intervals per type."""

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    #: Optional recall target applied when the strategy is bound to a path.
    desired_retention: float | None = Field(default=None, ge=0.7, le=0.99, allow_inf_nan=False)
    intervals: dict[KnowledgeType, list[StrictInt]]


class DuplicateReviewStrategyRequest(BaseModel):
    name: str = Field(default="", max_length=120)


class PathReviewStrategyRequest(BaseModel):
    """Bind a path to a strategy; an empty template_id restores the default."""

    template_id: str = ""


class ChapterImport(BaseModel):
    title: str
    knowledge_points: list[str] = []


class ImportFromBookRequest(BaseModel):
    chapters: list[ChapterImport]


class TopicSourceRequest(BaseModel):
    id: str = ""
    client_ref: str = ""
    kind: TopicSourceKind
    source_id: str = ""
    label: str = Field(..., min_length=1, max_length=200)
    excerpt: str = Field(default="", max_length=8_000)
    available: bool = True
    metadata: dict = Field(default_factory=dict)


class GenerateTopicDraftRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    goal: str = Field(..., min_length=1, max_length=2_000)
    sources: list[TopicSourceRequest] = Field(default_factory=list, max_length=16)
    #: Documents a previous draft left out. Sent when the learner asks for
    #: them to be covered, so the regeneration is told what was missed rather
    #: than being asked the same question and expected to answer differently.
    must_cover: list[str] = Field(default_factory=list, max_length=40)


class ConfirmTopicRequest(GenerateTopicDraftRequest):
    # The learner states a goal and picks materials; naming is not one more
    # box to fill before they can start. An empty name is derived from the
    # goal below, and stays renameable afterwards.
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=500)
    emoji: str = Field(default="🧭", max_length=16)
    # Optional: a mastery goal is created *before* it has an outline, and the
    # outline is then designed with the tutor in the goal's first session.
    # The region ceiling is the generator's, not a second opinion: a route
    # over a fourteen-document library legitimately has more than eight, and
    # this used to reject the very draft the server had just produced.
    modules: list[ModuleInput] = Field(default_factory=list, max_length=MAX_MODULE_LIMIT)


class EditTopicMapRequest(BaseModel):
    modules: list[ModuleInput] = Field(..., min_length=1, max_length=MAX_MODULE_LIMIT)


class LearnerOverrideRequest(BaseModel):
    mastered: bool
    note: str = Field(default="", max_length=500)


def _topic_sources(items: list[TopicSourceRequest]) -> list[TopicSource]:
    for item in items:
        if item.kind == TopicSourceKind.PROJECT_FILE:
            from deeptutor.learning.projects import read_upload

            try:
                parsed = read_upload(item.source_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422, detail="Uploaded material is unavailable in this workspace."
                ) from exc
            item.label = parsed["filename"]
            item.excerpt = parsed["text"][:8000]
            item.metadata = {"text_chars": len(parsed["text"])}
    return [
        TopicSource(
            id=item.id.strip() or f"source_{uuid.uuid4().hex}",
            kind=item.kind,
            source_id=item.source_id.strip()[:300],
            label=item.label.strip(),
            excerpt=item.excerpt.strip(),
            position=index,
            available=item.available,
            metadata=dict(item.metadata),
        )
        for index, item in enumerate(items)
    ]


def _topic_source_aliases(
    items: list[TopicSourceRequest], sources: list[TopicSource]
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item, source in zip(items, sources, strict=True):
        alias = item.client_ref.strip()
        if alias:
            if alias in aliases and aliases[alias] != source.id:
                raise ObjectiveRelationError(f"Source reference {alias!r} is ambiguous")
            aliases[alias] = source.id
    return aliases


def _module_payloads(modules: list[ModuleInput]) -> list[dict]:
    return [module.model_dump(mode="json") for module in modules]


def _relation_refs(
    inputs: list[ModuleInput], modules: list[LearningModule]
) -> dict[str, RelationRefs]:
    refs: dict[str, RelationRefs] = {}
    for input_module, module in zip(inputs, modules, strict=True):
        for input_point, point in zip(
            input_module.knowledge_points, module.knowledge_points, strict=True
        ):
            refs[point.id] = RelationRefs(
                client_ref=input_point.client_ref.strip(),
                prerequisite_refs=normalize_refs(
                    [*input_point.prerequisite_refs, *input_point.prerequisite_ids],
                    label="prerequisite",
                ),
                topic_source_refs=normalize_refs(
                    [*input_point.topic_source_refs, *input_point.topic_source_ids],
                    label="topic source",
                ),
            )
    return refs


def _review_queue(progress, *, now: float | None = None) -> list[dict]:
    from deeptutor.learning.review_strategy import scheduler_for_progress
    from deeptutor.learning.scheduler import review_sort_key

    moment = time.time() if now is None else now
    scheduler = scheduler_for_progress(progress)
    names = {kp.id: kp.name for module in progress.modules for kp in module.knowledge_points}
    return [
        {
            "id": task.id,
            "knowledge_point_id": task.knowledge_point_id,
            "knowledge_point_name": names.get(task.knowledge_point_id, ""),
            "knowledge_type": task.knowledge_type.value,
            "due_at": task.due_at,
            "priority": task.priority,
            "due": task.due_at <= moment,
            "forgetting_risk": round(task.forgetting_risk, 3),
            "reason": task.reason,
            "evidence_source": task.evidence_source,
            "evidence_id": task.evidence_id,
            "stability": round(task.state.stability, 3),
            "retrievability": round(scheduler.retrievability(task.state, now=moment), 3),
            "desired_retention": task.state.desired_retention,
            "lapse_count": task.state.lapse_count,
            "recent_failure": bool(
                task.state.consecutive_wrong
                or any(
                    error.knowledge_point_id == task.knowledge_point_id
                    and error.status in ("active", "retrying")
                    for error in progress.error_records
                )
            ),
        }
        for task in sorted(
            learning_policy.reviewable_tasks(progress),
            key=lambda item: review_sort_key(item, now=moment),
        )
    ]


def _read_projection(progress, *, now: float | None = None):
    """Build a fresh queue for reads without persisting hydration or time drift."""
    from deeptutor.learning.review_strategy import scheduler_for_progress

    projected = progress.model_copy(deep=True)
    moment = time.time() if now is None else now
    projected.review_queue = scheduler_for_progress(projected).build_review_queue(
        projected, now=moment
    )
    return projected


def _next_step_payload(store: LearningStore, path_id: str, progress) -> dict:
    interaction = (
        store.get_active_interaction(path_id) if progress.pending_question is not None else None
    )
    return _next_step_from_interaction(progress, interaction)


def _next_step_from_interaction(
    progress: LearningProgress,
    interaction: MasteryInteraction | None,
) -> dict:
    return learning_policy.next_objective(
        progress,
        pending_session_id=interaction.session_id if interaction is not None else "",
    ).to_dict()


def _review_settings_payload(progress: LearningProgress) -> dict:
    binding = progress.review_strategy
    strategy = None
    if binding is not None:
        strategy = {
            "template_id": binding.template_id,
            "name": binding.name,
            "builtin": binding.builtin,
            "desired_retention": binding.desired_retention,
            "intervals": {
                knowledge_type.value: list(rounds)
                for knowledge_type, rounds in binding.intervals.items()
            },
        }
    return {
        "desired_retention": progress.desired_retention,
        "scope": "path",
        "strategy": strategy,
    }


def _topic_payload_from_snapshot(
    progress: LearningProgress,
    topic: MasteryTopic,
    session_count: int,
    active_interaction: MasteryInteraction | None,
) -> dict:
    path_id = progress.book_id
    moment = time.time()
    projected = _read_projection(progress, now=moment)
    return {
        "path_id": path_id,
        "name": learning_policy.path_display_name(progress),
        "metadata": topic.metadata.model_dump(mode="json"),
        "sources": [source.model_dump(mode="json") for source in topic.sources],
        "path_revision": progress.version,
        "wenjin_enabled": progress.wenjin_enabled,
        "next": learning_policy.next_objective(
            projected,
            now=moment,
            pending_session_id=active_interaction.session_id if active_interaction else "",
        ).to_dict(),
        "map": learning_policy.map_summary(projected, now=moment),
        "reviews": _review_queue(projected, now=moment),
        "review_settings": _review_settings_payload(progress),
        # Who this goal is for. Null until intake has happened, which is also
        # what the dashboard renders as "not asked yet".
        "learner_profile": (
            progress.learner_profile.model_dump(mode="json")
            if progress.learner_profile is not None and not progress.learner_profile.is_empty()
            else None
        ),
        "session_count": session_count,
        "updated_at": progress.updated_at,
    }


def _topic_payload(store: LearningStore, path_id: str) -> dict:
    progress = store.load(path_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Mastery topic not found")
    topic = store.get_topic(path_id, progress=progress)
    if topic is None:  # pragma: no cover - a loaded path always synthesizes metadata
        raise HTTPException(status_code=404, detail="Mastery topic not found")
    active_interaction = (
        store.get_active_interaction(path_id) if progress.pending_question is not None else None
    )
    return _topic_payload_from_snapshot(
        progress,
        topic,
        len(store.list_session_ids(path_id)),
        active_interaction,
    )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/topics")
async def list_topics():
    store = LearningStore()
    topics = await asyncio.to_thread(
        lambda: [
            _topic_payload_from_snapshot(*snapshot)
            for snapshot in store.list_topic_snapshots(status="active")
        ]
    )
    return {"topics": topics}


@router.get("/topics/index")
async def list_topic_index():
    """Just enough to *name* a topic: id, title, emoji.

    The sidebar groups study conversations under their topic, and it refreshes
    on every stream end. ``/topics`` answers with each path's whole knowledge
    map, review queue and source excerpts — kilobytes per topic, none of which
    a group header renders. This is the same walk with the payload cut to what
    a label needs.

    Declared above ``/topics/{path_id}``: that route matches any single
    segment, so a literal path below it would never be reached.
    """
    store = LearningStore()
    return {
        "topics": await asyncio.to_thread(
            lambda: [
                {
                    "path_id": progress.book_id,
                    "name": learning_policy.path_display_name(progress),
                    "emoji": topic.metadata.emoji,
                }
                for progress, topic, _session_count, _interaction in store.list_topic_snapshots(
                    status="active"
                )
            ]
        )
    }


@router.get("/reading/records", response_model=ReadingLearningRecords)
async def list_reading_learning_records() -> ReadingLearningRecords:
    """Reading progress and extension activity for the current account."""

    store = LearningStore()
    return await asyncio.to_thread(store.list_reading_records)


@router.post("/topics/draft")
async def generate_topic_route(body: GenerateTopicDraftRequest):
    from deeptutor.learning.topic_generation import TopicGenerationError, generate_topic_draft

    try:
        return await generate_topic_draft(
            name=body.name,
            goal=body.goal,
            sources=_topic_sources(body.sources),
            language=get_response_language(),
            must_cover=body.must_cover,
        )
    except TopicGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


#: Longest provisional name derived from a goal. A goal is a paragraph; a name
#: has to fit on a card.
_PROVISIONAL_NAME_CHARS = 32


def _provisional_name(goal: str) -> str:
    """A card-sized name for a goal the learner did not name themselves.

    The goal's own opening clause, because that is the sentence they wrote and
    the one they will recognise in a list. Anything is better than the storage
    id, which is what an unnamed path displayed before goals could be created
    without an outline to borrow a module name from.
    """
    head = re.split(r"[\n。.!?！？;；]", str(goal or "").strip(), maxsplit=1)[0].strip()
    if not head:
        return ""
    if len(head) <= _PROVISIONAL_NAME_CHARS:
        return head
    return head[:_PROVISIONAL_NAME_CHARS].rstrip() + "…"


@router.post("/topics")
async def create_topic(body: ConfirmTopicRequest):
    from deeptutor.learning.topic_generation import (
        TopicGenerationError,
        materialize_modules,
    )

    path_id = f"topic_{uuid.uuid4().hex}"
    modules = []
    if body.modules:
        try:
            modules = materialize_modules(
                path_id,
                _module_payloads(body.modules),
                strict=True,
                module_limit=MAX_MODULE_LIMIT,
            )
        except TopicGenerationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    sources = _topic_sources(body.sources)
    source_aliases = _topic_source_aliases(body.sources, sources)
    store = LearningStore()
    metadata = TopicMetadata(
        path_id=path_id,
        goal=body.goal.strip(),
        description=body.description.strip(),
        emoji=body.emoji.strip() or "🧭",
        map_seed=store._default_map_seed(path_id),
    )
    # A name is a different object from a goal: the goal is the sentence they
    # wrote, the name is the label they will recognise in a list months later.
    # The task model writes it; the truncated goal stands in when it cannot.
    resolved_name = body.name.strip()
    if not resolved_name:
        from deeptutor.learning.topic_naming import suggest_topic_name

        resolved_name = await suggest_topic_name(
            body.goal,
            source_labels=[source.label for source in sources],
        ) or _provisional_name(body.goal)
    service = LearningService(store)
    try:
        progress = await asyncio.to_thread(
            service.create_topic,
            path_id,
            name=resolved_name,
            modules=modules,
            metadata=metadata,
            sources=sources,
            relation_refs=_relation_refs(body.modules, modules),
            source_aliases=source_aliases,
        )
    except ObjectiveRelationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = await asyncio.to_thread(_topic_payload, store, path_id)
    payload["path_revision"] = progress.version
    return payload


@router.get("/topics/{path_id}")
async def get_topic(path_id: str):
    _validate_book_id(path_id)
    return await asyncio.to_thread(_topic_payload, LearningStore(), path_id)


@router.get("/topics/{path_id}/review-settings")
async def get_review_settings(path_id: str):
    _validate_book_id(path_id)
    progress = await asyncio.to_thread(LearningStore().load, path_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Mastery topic not found")
    return _review_settings_payload(progress)


@router.put("/topics/{path_id}/review-settings")
async def update_review_settings(path_id: str, body: ReviewSettingsRequest):
    _validate_book_id(path_id)
    if not await asyncio.to_thread(LearningStore().exists, path_id):
        raise HTTPException(status_code=404, detail="Mastery topic not found")
    async with _exclusive_path_mutation(path_id):
        store = LearningStore()

        def update(tx):
            from deeptutor.learning.review_strategy import scheduler_for_progress

            if tx.progress.desired_retention == body.desired_retention and all(
                state.desired_retention == body.desired_retention
                for state in tx.progress.repetition_states.values()
            ):
                return
            scheduler_for_progress(tx.progress).set_desired_retention(
                tx.progress, body.desired_retention
            )
            tx.touch()
            tx.emit(
                "review.settings_changed",
                {"desired_retention": tx.progress.desired_retention},
            )

        try:
            await asyncio.to_thread(store.mutate, path_id, update)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Mastery topic not found") from exc
    return await asyncio.to_thread(_topic_payload, LearningStore(), path_id)


# ── Review strategy templates (#1908) ─────────────────────────────────────────


def _strategy_payload(template: ReviewStrategyTemplate) -> dict:
    return template.model_dump(mode="json")


def _resolve_review_strategy(
    store: LearningStore, template_id: str
) -> ReviewStrategyTemplate | None:
    """Find a strategy by id: preset templates in code, custom ones in the store."""
    from deeptutor.learning.review_strategy import builtin_templates

    builtin = builtin_templates().get(template_id)
    if builtin is not None:
        return builtin
    try:
        return store.get_review_strategy(template_id)
    except ValueError:
        # Ids that cannot be a stored template are simply unknown ones.
        return None


def _build_custom_template(
    body: ReviewStrategyRequest, *, template_id: str
) -> ReviewStrategyTemplate:
    try:
        return ReviewStrategyTemplate(
            template_id=template_id,
            name=body.name,
            description=body.description,
            desired_retention=body.desired_retention,
            intervals=body.intervals,
        )
    except PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc.errors())) from exc


@router.get("/review-strategies")
async def list_review_strategies():
    """Every strategy the learner can bind: presets first, then their own."""
    from deeptutor.learning.review_strategy import BUILTIN_TEMPLATE_IDS, builtin_templates

    store = LearningStore()
    presets = builtin_templates()
    customs = await asyncio.to_thread(store.list_review_strategies)
    strategies = [presets[template_id] for template_id in BUILTIN_TEMPLATE_IDS]
    strategies.extend(customs)
    return {"strategies": [_strategy_payload(template) for template in strategies]}


@router.post("/review-strategies", status_code=201)
async def create_review_strategy(body: ReviewStrategyRequest):
    store = LearningStore()
    template = _build_custom_template(body, template_id=f"custom-{uuid.uuid4().hex[:12]}")
    try:
        saved = await asyncio.to_thread(store.save_review_strategy, template)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _strategy_payload(saved)


@router.get("/review-strategies/{template_id}")
async def get_review_strategy(template_id: str):
    template = await asyncio.to_thread(_resolve_review_strategy, LearningStore(), template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Review strategy not found")
    return _strategy_payload(template)


@router.put("/review-strategies/{template_id}")
async def update_review_strategy(template_id: str, body: ReviewStrategyRequest):
    from deeptutor.learning.review_strategy import BUILTIN_TEMPLATE_IDS

    if template_id in BUILTIN_TEMPLATE_IDS:
        raise HTTPException(status_code=403, detail="Built-in preset strategies cannot be edited")
    store = LearningStore()

    def _load_existing() -> ReviewStrategyTemplate | None:
        try:
            return store.get_review_strategy(template_id)
        except ValueError:
            return None

    existing = await asyncio.to_thread(_load_existing)
    if existing is None:
        raise HTTPException(status_code=404, detail="Review strategy not found")
    replacement = _build_custom_template(body, template_id=template_id)
    replacement.created_at = existing.created_at
    try:
        saved = await asyncio.to_thread(store.save_review_strategy, replacement)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _strategy_payload(saved)


@router.post("/review-strategies/{template_id}/duplicate", status_code=201)
async def duplicate_review_strategy(template_id: str, body: DuplicateReviewStrategyRequest):
    source = await asyncio.to_thread(_resolve_review_strategy, LearningStore(), template_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Review strategy not found")
    copy = ReviewStrategyTemplate(
        template_id=f"custom-{uuid.uuid4().hex[:12]}",
        name=body.name.strip() or f"{source.name} (copy)",
        description=source.description,
        desired_retention=source.desired_retention,
        intervals=source.intervals,
    )
    saved = await asyncio.to_thread(LearningStore().save_review_strategy, copy)
    return _strategy_payload(saved)


@router.delete("/review-strategies/{template_id}")
async def delete_review_strategy(template_id: str):
    from deeptutor.learning.review_strategy import BUILTIN_TEMPLATE_IDS

    if template_id in BUILTIN_TEMPLATE_IDS:
        raise HTTPException(status_code=403, detail="Built-in preset strategies cannot be deleted")

    def _delete() -> bool:
        try:
            return LearningStore().delete_review_strategy(template_id)
        except ValueError:
            return False

    if not await asyncio.to_thread(_delete):
        raise HTTPException(status_code=404, detail="Review strategy not found")
    return {"deleted": True}


@router.put("/topics/{path_id}/review-strategy")
async def update_path_review_strategy(path_id: str, body: PathReviewStrategyRequest):
    """Bind which strategy a mastery path schedules with.

    The binding snapshots the template's intervals onto the path, so later
    edits to the template do not rewrite existing schedules. A strategy that
    carries a recall target applies it. Every bind or switch replays durable
    evidence under the new interval snapshot; unbinding keeps the learner's
    current target.
    """
    _validate_book_id(path_id)
    store = LearningStore()
    if not await asyncio.to_thread(store.exists, path_id):
        raise HTTPException(status_code=404, detail="Mastery topic not found")
    template: ReviewStrategyTemplate | None = None
    if body.template_id:
        template = await asyncio.to_thread(_resolve_review_strategy, store, body.template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="Review strategy not found")
    async with _exclusive_path_mutation(path_id):
        store = LearningStore()

        def update(tx):
            from deeptutor.learning.review_strategy import scheduler_for_progress

            retention = template.desired_retention if template is not None else None
            current = tx.progress.review_strategy
            replacement = (
                template.as_binding(bound_at=current.bound_at if current is not None else None)
                if template is not None
                else None
            )
            if current == replacement and (
                retention is None or retention == tx.progress.desired_retention
            ):
                return
            if template is None:
                tx.progress.review_strategy = None
            else:
                tx.progress.review_strategy = replacement
                retention = template.desired_retention
            if retention is not None:
                tx.progress.desired_retention = retention
            scheduler_for_progress(tx.progress).rebuild_from_evidence(tx.progress)
            tx.touch()
            tx.emit(
                "review.strategy_changed",
                {
                    "template_id": template.template_id if template is not None else "",
                    "name": template.name if template is not None else "",
                },
            )

        try:
            await asyncio.to_thread(store.mutate, path_id, update)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Mastery topic not found") from exc
    return await asyncio.to_thread(_topic_payload, LearningStore(), path_id)


@router.put("/topics/{path_id}/map")
async def edit_topic_map(path_id: str, body: EditTopicMapRequest):
    _validate_book_id(path_id)
    from deeptutor.learning.topic_generation import (
        TopicGenerationError,
        materialize_modules,
    )

    async with _exclusive_path_mutation(path_id):
        store = LearningStore()
        progress = await asyncio.to_thread(store.load, path_id)
        if progress is None:
            raise HTTPException(status_code=404, detail="Mastery topic not found")
        existing_module_ids = {module.id for module in progress.modules}
        existing_objective_ids = {
            point.id for module in progress.modules for point in module.knowledge_points
        }
        try:
            modules = materialize_modules(
                path_id,
                _module_payloads(body.modules),
                strict=True,
                existing_module_ids=existing_module_ids,
                existing_objective_ids=existing_objective_ids,
                existing_objective_counts={
                    module.id: len(module.knowledge_points) for module in progress.modules
                },
                module_limit=MAX_MODULE_LIMIT,
            )
        except TopicGenerationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            await asyncio.to_thread(
                LearningService(store).replace_modules_for_path,
                path_id,
                modules,
                event_type="topic.map_edited",
                relation_refs=_relation_refs(body.modules, modules),
            )
        except ObjectiveRelationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await asyncio.to_thread(_topic_payload, LearningStore(), path_id)


@router.post("/topics/{path_id}/objectives/{kp_id}/override")
async def set_learner_override(
    path_id: str,
    kp_id: str,
    body: LearnerOverrideRequest,
):
    _validate_book_id(path_id)
    async with _exclusive_path_mutation(path_id):
        try:
            progress = await asyncio.to_thread(
                LearningService(LearningStore()).set_learner_mastery_override,
                path_id,
                kp_id,
                mastered=body.mastered,
                note=body.note,
            )
        except Exception as exc:
            from deeptutor.learning.service import MasteryInteractionError

            if isinstance(exc, MasteryInteractionError):
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise
    return {
        "status": "ok",
        "path_revision": progress.version,
        "map": learning_policy.map_summary(progress),
    }


@router.get("/topics/{path_id}/sessions")
async def list_topic_sessions(path_id: str):
    _validate_book_id(path_id)
    learning_store = LearningStore()
    if not await asyncio.to_thread(learning_store.exists, path_id):
        raise HTTPException(status_code=404, detail="Mastery topic not found")
    # The same walk chat's navigation tools use (``learning.navigation``), so
    # the atlas screen and a hand-off card can never disagree about which
    # conversations a topic has.
    from deeptutor.learning.navigation import topic_sessions

    return {
        "path_id": path_id,
        "sessions": await topic_sessions(path_id, store=learning_store),
    }


class SetSessionModeRequest(BaseModel):
    mode: str = Field(..., max_length=32)


@router.put("/topics/{path_id}/sessions/{session_id}/mode")
async def set_session_mode(path_id: str, session_id: str, body: SetSessionModeRequest):
    """Change what a conversation is doing, from the learner's own buttons.

    The same move the tutor makes with ``mastery_mode``, through the same
    admission rule — so pressing "Study" on a goal with no outline is refused
    with the sentence the tutor would have said, rather than silently putting
    the conversation somewhere its tools will then refuse to work.
    """
    from deeptutor.capabilities.mastery.mode import MODES, admission_error, normalize_mode

    _validate_book_id(path_id)
    requested = str(body.mode or "").strip().lower()
    if requested not in MODES:
        raise HTTPException(
            status_code=422,
            detail=f"mode must be one of: {', '.join(MODES)}",
        )

    store = LearningStore()
    progress = await asyncio.to_thread(store.load, path_id)
    has_outline = progress is not None and any(
        module.knowledge_points for module in progress.modules
    )
    refusal = admission_error(requested, has_outline=has_outline)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    from deeptutor.services.session import get_session_store

    session_store = get_session_store()
    if await session_store.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await session_store.update_session_preferences(session_id, {"mastery_session_mode": requested})
    return {"session_id": session_id, "mode": normalize_mode(requested)}


@router.get("/topics/{path_id}/ask-hint")
async def get_topic_ask_hint(path_id: str, session_id: str = ""):
    """One question the learner could ask here, for the composer placeholder.

    Written by the task model, never blocking: an empty ``hint`` means the
    composer keeps the static placeholder it has always had.
    """
    _validate_book_id(path_id)
    from deeptutor.services.mastery_hints import get_ask_hint

    return await get_ask_hint(path_id, session_id)


@ws_router.websocket("/mastery-paths")
async def mastery_topic_websocket(ws: WebSocket) -> None:
    """Subscribe to one living topic with durable revision replay."""

    from deeptutor.api.routers.auth import ws_auth_failed, ws_require_auth
    from deeptutor.learning.event_hub import mastery_topic_event_hub
    from deeptutor.multi_user.context import reset_current_user

    user_token = await ws_require_auth(ws)
    if user_token is ws_auth_failed:
        return
    await ws.accept()
    send_lock = asyncio.Lock()
    subscription = None
    forward_task: asyncio.Task | None = None
    cursor = 0

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def stop_forwarding() -> None:
        nonlocal subscription, forward_task
        if subscription is not None:
            subscription.close()
            subscription = None
        if forward_task is not None:
            forward_task.cancel()
            with suppress(asyncio.CancelledError):
                await forward_task
            forward_task = None

    async def forward(path_id: str, store: LearningStore) -> None:
        nonlocal cursor
        assert subscription is not None
        while True:
            signal = await subscription.get()
            events = await asyncio.to_thread(
                store.list_events,
                path_id,
                after_revision=cursor,
            )
            if events:
                cursor = max(cursor, max(event.revision for event in events))
            elif signal.revision <= cursor and signal.reason not in _SCREEN_ONLY_SIGNALS:
                continue
            cursor = max(cursor, signal.revision)
            await send(
                {
                    "type": "topic_event",
                    "path_id": path_id,
                    "revision": cursor,
                    "reason": signal.reason,
                    "sequence": signal.sequence,
                    "events": [event.model_dump(mode="json") for event in events],
                }
            )

    try:
        while True:
            try:
                message = await ws.receive_json()
            except WebSocketDisconnect:
                break
            message_type = str(message.get("type") or "").strip()
            if message_type != "subscribe":
                await send({"type": "error", "content": "Expected a subscribe message"})
                continue
            path_id = str(message.get("path_id") or "").strip()
            try:
                _validate_book_id(path_id)
            except HTTPException:
                await send({"type": "error", "content": "Invalid path_id"})
                continue
            store = LearningStore()
            if not await asyncio.to_thread(store.exists, path_id):
                await send({"type": "error", "content": "Mastery topic not found"})
                continue

            await stop_forwarding()
            requested_cursor = max(0, int(message.get("after_revision") or 0))
            progress = await asyncio.to_thread(store.load, path_id)
            current_revision = int(progress.version if progress else 0)
            # A stale browser cache must not be able to pin the subscription
            # beyond the server's durable head and suppress future updates.
            cursor = min(requested_cursor, current_revision)
            # Register before replay. A concurrent commit is therefore either
            # present in this DB tail, queued on the subscription, or both;
            # the cursor in ``forward`` removes the harmless overlap.
            subscription = mastery_topic_event_hub.subscribe(
                path_id,
                scope=store.event_scope,
            )
            events = await asyncio.to_thread(
                store.list_events,
                path_id,
                after_revision=cursor,
            )
            if events:
                cursor = max(cursor, max(event.revision for event in events))
            cursor = max(cursor, current_revision)
            await send(
                {
                    "type": "subscribed",
                    "path_id": path_id,
                    "revision": cursor,
                    "events": [event.model_dump(mode="json") for event in events],
                }
            )
            forward_task = asyncio.create_task(forward(path_id, store))
    finally:
        await stop_forwarding()
        reset_current_user(user_token)


@router.get("/progress")
async def list_all_progress():
    service = get_learning_service()
    return await asyncio.to_thread(service.list_progress)


@router.get("/progress/{book_id}")
async def get_progress(book_id: str):
    _validate_book_id(book_id)
    service = get_learning_service()
    progress = await asyncio.to_thread(service.store.load, book_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Mastery progress not found")
    payload = progress.model_dump(mode="json")
    if progress.pending_question is not None:
        from deeptutor.learning.pending import public_pending_question

        payload["pending_question"] = public_pending_question(progress.pending_question).to_dict()
    return payload


@router.get("/progress/{book_id}/map")
async def get_progress_map(book_id: str):
    """The dashboard view of a path: the gate-decided next step plus a map of
    every objective's status (new / learning / mastered). The per-type gate
    lives in ``learning.policy`` so the dashboard and the tutor agree."""
    _validate_book_id(book_id)
    service = get_learning_service()
    progress = service.get_or_create(book_id)
    moment = time.time()
    progress = _read_projection(progress, now=moment)
    return {
        "book_id": book_id,
        "name": learning_policy.path_display_name(progress),
        "path_revision": progress.version,
        "next": learning_policy.next_objective(progress, now=moment).to_dict(),
        "map": learning_policy.map_summary(progress, now=moment),
    }


@router.get("/progress/{book_id}/board")
async def get_progress_board(book_id: str):
    """The visual learning board: every knowledge point as a card, enriched
    with its next review time and a deterministic grid position derived from
    the module order. A read-only projection of the same mastery data the
    tutor and the map view use."""
    _validate_book_id(book_id)
    service = get_learning_service()
    progress = service.get_or_create(book_id)
    moment = time.time()
    progress = _read_projection(progress, now=moment)
    summary = learning_policy.map_summary(progress, now=moment)

    due_by_kp = {task.knowledge_point_id: task.due_at for task in progress.review_queue}

    cards: list[dict] = []
    modules: list[dict] = []
    for module in summary["modules"]:
        module_cards: list[dict] = []
        for index, kp in enumerate(module["knowledge_points"]):
            card = {
                "id": kp["id"],
                "name": kp["name"],
                "type": kp["type"],
                "module_id": module["id"],
                "module_name": module["name"],
                "status": kp["status"],
                "mastery_level": kp["mastery"],
                "next_review_at": due_by_kp.get(kp["id"]),
                "position": {"column": module["order"], "row": index},
            }
            cards.append(card)
            module_cards.append(card)
        modules.append(
            {
                "id": module["id"],
                "name": module["name"],
                "order": module["order"],
                "mastered": module["mastered"],
                "total": module["total"],
                "cards": module_cards,
            }
        )

    return {
        "book_id": book_id,
        "name": summary["name"],
        "path_revision": progress.version,
        "cards": cards,
        "modules": modules,
    }


@router.get("/progress/{book_id}/objectives/{kp_id}")
async def get_objective_report(book_id: str, kp_id: str):
    """The evidence behind one objective: attempts, schedule, errors, prompts.

    ``policy.objective_report`` is pure over the aggregate, so the questions
    themselves — which live in the durable interaction log, not the aggregate —
    are joined on here, redacted of their answer keys.
    """
    _validate_book_id(book_id)
    store = LearningStore()
    progress, evidence, evidence_count = await asyncio.to_thread(
        store.load_with_learning_evidence, book_id, kp_id, limit=20
    )
    if progress is None:
        raise HTTPException(status_code=404, detail="Progress not found")
    moment = time.time()
    progress = _read_projection(progress, now=moment)
    report = learning_policy.objective_report(progress, kp_id, now=moment)
    if report is None:
        raise HTTPException(status_code=404, detail="Objective not found")

    from deeptutor.learning.pending import public_pending_question

    interactions = await asyncio.to_thread(store.list_interactions, book_id)
    prompts = {
        interaction.interaction_id: public_pending_question(interaction.question).prompt
        for interaction in interactions
    }
    for attempt in report["attempts"]:
        attempt["prompt"] = prompts.get(attempt["question_id"], "")
    report["evidence"] = [item.model_dump(mode="json") for item in evidence]
    report["evidence_count"] = evidence_count
    return {"book_id": book_id, "path_revision": progress.version, "objective": report}


@router.get("/progress/{book_id}/events")
async def get_progress_events(book_id: str, after_revision: int = 0):
    """Ordered, redacted domain events for reconnect and incremental UI sync."""
    _validate_book_id(book_id)
    store = LearningStore()
    progress = await asyncio.to_thread(store.load, book_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Progress not found")
    events = await asyncio.to_thread(
        store.list_events,
        book_id,
        after_revision=max(0, after_revision),
    )
    return {
        "book_id": book_id,
        "events": [event.model_dump(mode="json") for event in events],
    }


@router.get("/progress/{book_id}/sessions")
async def get_progress_sessions(book_id: str):
    """Expose the explicit conversation associations for this path."""
    _validate_book_id(book_id)
    store = LearningStore()
    if not await asyncio.to_thread(store.exists, book_id):
        raise HTTPException(status_code=404, detail="Progress not found")
    session_ids = await asyncio.to_thread(store.list_session_ids, book_id)
    return {"book_id": book_id, "session_ids": session_ids}


@router.post("/progress/{book_id}/init-modules")
async def init_modules(book_id: str, body: InitModulesRequest):
    _validate_book_id(book_id)
    modules = _parse_modules(_module_payloads(body.modules))
    _validate_runnable_modules(modules)
    async with _exclusive_path_mutation(book_id):
        service = get_learning_service()
        progress = await asyncio.to_thread(service.replace_modules_for_path, book_id, modules)
    return {
        "status": "ok",
        "module_count": len(modules),
        "path_revision": progress.version,
    }


@router.post("/progress/{book_id}/import-from-book")
async def import_from_book(book_id: str, body: ImportFromBookRequest):
    _validate_book_id(book_id)
    modules = []
    for i, ch in enumerate(body.chapters):
        kps = [
            KnowledgePoint(
                id=f"{book_id}_ch{i}_kp{j}",
                name=kp_name,
                type=KnowledgeType("concept"),
                module_id=f"{book_id}_ch{i}",
            )
            for j, kp_name in enumerate(ch.knowledge_points)
        ]
        modules.append(
            LearningModule(
                id=f"{book_id}_ch{i}",
                name=ch.title or f"Chapter {i + 1}",
                order=i,
                pass_threshold=0.7,
                knowledge_points=kps,
            )
        )
    _validate_runnable_modules(modules)
    async with _exclusive_path_mutation(book_id):
        service = get_learning_service()
        progress = await asyncio.to_thread(service.replace_modules_for_path, book_id, modules)
    return {
        "status": "ok",
        "module_count": len(modules),
        "path_revision": progress.version,
    }


@router.patch("/progress/{book_id}")
async def rename_progress(book_id: str, body: RenamePathRequest):
    """Rename a path — the only edit that is the learner's rather than the tutor's.

    Guarded like every other path mutation so a rename cannot interleave with a
    tutoring turn's own commit, and emitted as an event so the activity feed
    records who called it what.
    """
    _validate_book_id(book_id)
    store = LearningStore()
    if not await asyncio.to_thread(store.exists, book_id):
        raise HTTPException(status_code=404, detail="Progress not found")
    async with _exclusive_path_mutation(book_id):
        progress = await asyncio.to_thread(LearningService(store).rename_path, book_id, body.name)
    return {
        "status": "ok",
        "name": learning_policy.path_display_name(progress),
        "path_revision": progress.version,
    }


@router.delete("/progress/{book_id}")
async def delete_progress(book_id: str):
    _validate_book_id(book_id)
    store = LearningStore()
    if not await asyncio.to_thread(store.exists, book_id):
        raise HTTPException(status_code=404, detail="Progress not found")
    async with _exclusive_path_mutation(book_id):
        await asyncio.to_thread(store.delete, book_id)
    return {"status": "ok"}


@router.post("/progress/{book_id}/skip-question")
async def skip_pending_question(book_id: str):
    """Drop an outstanding question the learner can no longer answer.

    The narrow escape hatch for a path stalled on ``answer_pending``; unlike
    ``redo`` it keeps every mastery level and review the learner has earned.
    """
    _validate_book_id(book_id)
    store = LearningStore()
    if not await asyncio.to_thread(store.exists, book_id):
        raise HTTPException(status_code=404, detail="Progress not found")
    async with _exclusive_path_mutation(book_id):
        progress, skipped = await asyncio.to_thread(
            LearningService(store).abandon_active_question, book_id
        )
    return {"status": "ok", "skipped": skipped, "path_revision": progress.version}


@router.post("/progress/{book_id}/redo")
async def redo_progress(book_id: str):
    _validate_book_id(book_id)
    store = LearningStore()
    if not await asyncio.to_thread(store.exists, book_id):
        raise HTTPException(status_code=404, detail="Progress not found")
    async with _exclusive_path_mutation(book_id):
        progress = await asyncio.to_thread(LearningService(store).reset_path, book_id)
    return {"status": "ok", "path_revision": progress.version}


class NotebookRecordInput(BaseModel):
    id: str
    type: str = "note"
    title: str = ""
    output: str = ""


class GenerateFromNotebookRequest(BaseModel):
    notebook_id: str
    records: list[NotebookRecordInput]


class GenerateFromReadingRequest(BaseModel):
    workspace_id: str
    material_ids: list[str] = Field(default_factory=list, max_length=20)


@router.post("/progress/{book_id}/generate-from-notebook")
async def generate_from_notebook(book_id: str, body: GenerateFromNotebookRequest):
    _validate_book_id(book_id)
    if not body.records:
        raise HTTPException(status_code=400, detail="No records provided")

    records_data = [
        {
            "type": html.escape(r.type[:50], quote=False),
            "title": html.escape(r.title[:200], quote=False),
            "output": html.escape(r.output[:500], quote=False),
        }
        for r in body.records[:20]
    ]
    records_json = json.dumps(records_data, ensure_ascii=False)
    from deeptutor.services.llm import complete

    language = get_response_language()
    system_prompt, prompt = learning_prompts.notebook_generation_prompts(language, records_json)
    response = await complete(prompt=prompt, system_prompt=system_prompt)
    # LLMs commonly fence/slightly-malform JSON; use the shared fence-stripping
    # repair parser instead of bare json.loads so the common case isn't a 502.
    data = parse_json_response(response, fallback=None)
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="LLM returned invalid JSON")

    modules_raw = data.get("modules", [])
    if not isinstance(modules_raw, list):
        raise HTTPException(
            status_code=502, detail="LLM returned invalid structure: modules is not a list"
        )
    _ALLOWED_KP_TYPES = {"memory", "concept", "procedure", "design"}
    modules = []
    for i, m in enumerate(modules_raw):
        if not isinstance(m, dict) or "name" not in m:
            continue
        fallback_name = learning_prompts.default_module_name(language, i + 1)
        module_name = str(m.get("name") or fallback_name).strip()[:200] or fallback_name
        kps = []
        for j, kp in enumerate(m.get("knowledge_points", [])):
            if not isinstance(kp, dict) or "name" not in kp:
                continue
            kp_name = str(kp["name"]).strip()[:200]
            if len(kp_name) < 2:
                continue
            kp_type = str(kp.get("type", "concept")).strip()
            if kp_type not in _ALLOWED_KP_TYPES:
                kp_type = "concept"
            kps.append(
                KnowledgePoint(
                    id=f"{book_id}_nb{i}_kp{j}",
                    name=kp_name,
                    type=KnowledgeType(kp_type),
                    module_id=f"{book_id}_nb{i}",
                )
            )
        modules.append(
            LearningModule(
                id=f"{book_id}_nb{i}",
                name=module_name,
                order=i,
                pass_threshold=0.7,
                knowledge_points=kps,
            )
        )
    _validate_runnable_modules(modules, status_code=502)
    async with _exclusive_path_mutation(book_id):
        service = get_learning_service()
        progress = await asyncio.to_thread(service.replace_modules_for_path, book_id, modules)
    return {
        "status": "ok",
        "module_count": len(modules),
        "modules": [m.model_dump() for m in modules],
        "path_revision": progress.version,
    }


@router.post("/progress/{book_id}/generate-from-reading")
async def generate_from_reading(book_id: str, body: GenerateFromReadingRequest):
    """Create a mastery curriculum from a private reading workspace."""
    from deeptutor.reading.knowledge_capture import mastery_source_records

    try:
        records = await asyncio.to_thread(
            mastery_source_records,
            body.workspace_id,
            material_ids=body.material_ids,
        )
    except Exception as exc:
        from deeptutor.reading import ReadingError

        if isinstance(exc, ReadingError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise
    return await generate_from_notebook(
        book_id,
        GenerateFromNotebookRequest(
            notebook_id=f"reading:{body.workspace_id}",
            records=[NotebookRecordInput(**record) for record in records],
        ),
    )


class WenjinConsentRequest(BaseModel):
    enabled: bool


@router.get("/topics/{path_id}/wenjin")
async def get_wenjin_state(path_id: str):
    from deeptutor.learning.wenjin import learner_continuity, state_summary

    store = LearningStore()
    progress = await asyncio.to_thread(store.load, path_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Learning project not found.")
    state = await asyncio.to_thread(state_summary, progress, store=store)
    interaction = await asyncio.to_thread(store.get_active_interaction, path_id)
    state["resume_session_id"] = interaction.session_id if interaction else None
    state["prior_learning"] = await asyncio.to_thread(learner_continuity, store, progress)
    return state


@router.post("/topics/{path_id}/wenjin/enable")
async def enable_wenjin(path_id: str):
    from deeptutor.learning.wenjin import enable_path, state_summary

    try:
        progress = await asyncio.to_thread(enable_path, LearningStore(), path_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return state_summary(progress)


@router.put("/topics/{path_id}/wenjin/consent")
async def update_wenjin_consent(path_id: str, body: WenjinConsentRequest):
    from deeptutor.learning.evolution import set_consent
    from deeptutor.learning.wenjin import state_summary

    store = LearningStore()
    if not store.exists(path_id):
        raise HTTPException(status_code=404, detail="Learning project not found.")
    progress = await asyncio.to_thread(set_consent, store, path_id, body.enabled)
    return state_summary(progress)


@router.post("/topics/{path_id}/wenjin/rollback")
async def rollback_wenjin_strategy(path_id: str):
    from deeptutor.learning.evolution import rollback
    from deeptutor.learning.wenjin import state_summary

    store = LearningStore()
    if not store.exists(path_id):
        raise HTTPException(status_code=404, detail="Learning project not found.")
    try:
        progress = await asyncio.to_thread(rollback, store, path_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state_summary(progress)


@router.post("/project-materials")
async def upload_project_material(file: UploadFile = File(...)):
    from deeptutor.learning.projects import MAX_UPLOAD_BYTES, import_file

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        source = await import_file(file.filename or "document.txt", data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return source.model_dump(mode="json")


class AttachProjectMaterialRequest(BaseModel):
    source_id: str = Field(min_length=32, max_length=32)


@router.post("/topics/{path_id}/project-materials")
async def attach_project_material(path_id: str, body: AttachProjectMaterialRequest):
    from deeptutor.learning.projects import attach_upload

    try:
        source = await asyncio.to_thread(attach_upload, LearningStore(), path_id, body.source_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return source.model_dump(mode="json")


@router.get("/topics/{path_id}/project")
async def get_learning_project(path_id: str):
    store = LearningStore()
    progress = await asyncio.to_thread(store.load, path_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Learning project not found")
    return {
        "topic": await asyncio.to_thread(_topic_payload, store, path_id),
        "location": progress.learning_location,
        "chapters": {
            key: {"created_at": value.get("created_at")}
            for key, value in progress.chapter_materials.items()
        },
    }


@router.post("/topics/{path_id}/chapters/{chapter_id}")
async def prepare_project_chapter(path_id: str, chapter_id: str):
    from deeptutor.learning.projects import generate_chapter

    store = LearningStore()
    if not await asyncio.to_thread(store.exists, path_id):
        raise HTTPException(status_code=404, detail="Learning project not found")
    try:
        return await generate_chapter(store, path_id, chapter_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Chapter generation is unavailable. Check the language model settings and retry.",
        ) from exc


class ProjectLocationRequest(BaseModel):
    surface: str = Field(default="reading", pattern="^(reading|tutoring)$")
    chapter_id: str = Field(min_length=1, max_length=300)
    position: float = Field(ge=0, le=1, allow_inf_nan=False)


@router.put("/topics/{path_id}/project/location")
async def update_project_location(path_id: str, body: ProjectLocationRequest):
    from deeptutor.learning.projects import save_location

    try:
        return await asyncio.to_thread(
            save_location, LearningStore(), path_id, body.chapter_id, body.position, body.surface
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/topics/{path_id}/diagnostic-facts")
async def get_diagnostic_facts(path_id: str):
    """Read native diagnostic facts only; no assessment or mastery mutation."""
    from deeptutor.learning.diagnosis import load_project_facts

    try:
        facts = await asyncio.to_thread(load_project_facts, LearningStore(), path_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return facts.model_dump(mode="json")


class DiagnosticPreferenceRequest(BaseModel):
    strategy: Literal["teach_first", "probe_first"]


@router.put("/topics/{path_id}/wenjin/preference")
async def set_diagnostic_preference(path_id: str, body: DiagnosticPreferenceRequest):
    from deeptutor.learning.wenjin import state_summary

    store = LearningStore()
    if not await asyncio.to_thread(store.exists, path_id):
        raise HTTPException(status_code=404, detail="Learning project not found")
    progress, _ = await asyncio.to_thread(
        LearningService(store).record_learner_profile,
        path_id,
        fields={"teaching_strategy": body.strategy},
        learner_message="Explicit learning-sequence preference selected in UI: " + body.strategy,
    )
    return state_summary(progress)


# Existing result import only: all operations inherit authenticated owner/workspace scope.


async def _external_import_call(path_id: str, operation, *args):
    from deeptutor.learning.external_diagnoses import ExternalImportConflict

    store = LearningStore()
    if not await asyncio.to_thread(store.exists, path_id):
        raise HTTPException(status_code=404, detail="Learning project not found")
    try:
        return await asyncio.to_thread(operation, store, path_id, *args)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExternalImportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/topics/{path_id}/diagnostic-imports/preview")
async def preview_external_diagnosis(path_id: str, body: ExternalPreviewRequest):
    from deeptutor.learning.external_diagnoses import preview_import

    return await _external_import_call(path_id, preview_import, body)


@router.post("/topics/{path_id}/diagnostic-imports")
async def commit_external_diagnosis(path_id: str, body: ExternalCommitRequest):
    from deeptutor.learning.external_diagnoses import commit_import

    return await _external_import_call(path_id, commit_import, body)


@router.get("/topics/{path_id}/diagnostic-imports")
async def get_external_diagnoses(path_id: str):
    from deeptutor.learning.external_diagnoses import list_imports

    return await _external_import_call(path_id, list_imports)


@router.post("/topics/{path_id}/diagnostic-imports/{import_id}/revoke")
async def revoke_external_diagnosis(path_id: str, import_id: str):
    from deeptutor.learning.external_diagnoses import revoke_import

    return await _external_import_call(path_id, revoke_import, import_id)


@router.get("/topics/{path_id}/diagnostic-impact")
async def get_diagnostic_import_impact(path_id: str):
    from deeptutor.learning.diagnostic_impact import load_import_impact

    return await _external_import_call(path_id, load_import_impact)


class DiagnosticDatasetRequest(BaseModel):
    source_revision: int
    excluded_ids: list[str] = Field(default_factory=list, max_length=10000)
    synthetic: bool = False


async def _dataset_call(path_id, function, *args):
    try:
        return await asyncio.to_thread(function, LearningStore(), path_id, *args)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/topics/{path_id}/diagnosis/evidence")
async def diagnostic_catalogue(path_id: str):
    from deeptutor.learning.diagnostic_datasets import catalogue

    return await _dataset_call(path_id, catalogue)


@router.get("/topics/{path_id}/diagnosis/datasets")
async def diagnostic_datasets(path_id: str):
    from deeptutor.learning.diagnostic_datasets import snapshots

    return await _dataset_call(path_id, snapshots)


@router.post("/topics/{path_id}/diagnosis/datasets")
async def diagnostic_dataset_create(path_id: str, body: DiagnosticDatasetRequest):
    from deeptutor.learning.diagnostic_datasets import create_snapshot

    return await _dataset_call(
        path_id, create_snapshot, body.source_revision, body.excluded_ids, body.synthetic
    )


@router.get("/topics/{path_id}/diagnosis/datasets/{snapshot_id}")
async def diagnostic_dataset_get(path_id: str, snapshot_id: str):
    from deeptutor.learning.diagnostic_datasets import snapshots

    return await _dataset_call(path_id, snapshots, snapshot_id)


@router.get("/topics/{path_id}/diagnosis/source")
async def diagnostic_source(path_id: str, observation_id: str):
    from deeptutor.api.routers.sessions import get_session
    from deeptutor.learning.diagnostic_datasets import catalogue

    data = await _dataset_call(path_id, catalogue)
    row = next((r for r in data["rows"] if r["observation_id"] == observation_id), None)
    if row is None or not row["session_id"]:
        raise HTTPException(status_code=404, detail="没有可定位的原对话")
    session = await get_session(row["session_id"])
    from deeptutor.services.session import get_session_store

    turn = await get_session_store().get_turn(row["turn_id"]) if row["turn_id"] else None
    assistant_id = (
        turn.get("assistant_message_id")
        if turn and turn.get("session_id") == row["session_id"]
        else None
    )
    messages = []
    for m in session.get("messages", []):
        if m.get("role") not in ("user", "assistant"):
            continue
        meta = m.get("metadata") or {}
        matched = bool(
            (assistant_id is not None and m.get("id") == assistant_id)
            or row["turn_id"]
            and (
                m.get("turn_id") == row["turn_id"]
                or meta.get("turn_id") == row["turn_id"]
                or any(
                    (e.get("metadata") or {}).get("turn_id") == row["turn_id"]
                    for e in (m.get("events") or [])
                    if isinstance(e, dict)
                )
            )
        )
        messages.append(
            {
                "id": m.get("id"),
                "role": m.get("role"),
                "content": m.get("content", "")
                if isinstance(m.get("content", ""), str)
                else json.dumps(m.get("content"), ensure_ascii=False),
                "matched": matched,
            }
        )
    return {
        "session_id": row["session_id"],
        "title": session.get("title", "原对话"),
        "turn_id": row["turn_id"],
        "messages": messages,
        "located": any(m["matched"] for m in messages),
    }


class PersonalTrainingRequest(BaseModel):
    source_revision: int = Field(ge=0)


@router.get("/topics/{path_id}/diagnosis/training")
async def personal_training_runs(path_id: str):
    from deeptutor.learning.personal_training import runs

    return await _dataset_call(path_id, runs)


@router.post("/topics/{path_id}/diagnosis/training")
async def personal_training_start(path_id: str, body: PersonalTrainingRequest):
    from deeptutor.learning.training_jobs import submit

    return await _dataset_call(path_id, submit, body.source_revision)


class PlanRollbackRequest(BaseModel):
    event_id: int
    source_revision: int = Field(ge=0)


@router.get("/topics/{path_id}/diagnosis/plans")
async def diagnosis_plan_history(path_id: str):
    from deeptutor.learning.plan_versions import history

    return await _dataset_call(path_id, history)


@router.post("/topics/{path_id}/diagnosis/plans/rollback")
async def diagnosis_plan_rollback(path_id: str, body: PlanRollbackRequest):
    from deeptutor.learning.plan_versions import rollback

    return await _dataset_call(path_id, rollback, body.event_id, body.source_revision)


@router.get("/topics/{path_id}/diagnosis/training/jobs")
async def personal_training_jobs(path_id: str):
    from deeptutor.learning.training_jobs import list_jobs

    return await _dataset_call(path_id, list_jobs)


class ModelRollbackRequest(BaseModel):
    run_id: str


@router.post("/topics/{path_id}/diagnosis/training/rollback")
async def personal_model_rollback(path_id: str, body: ModelRollbackRequest):
    from deeptutor.learning.personal_training import rollback_parameters

    return await _dataset_call(path_id, rollback_parameters, body.run_id)


class LearningHelpRequest(BaseModel):
    kind: Literal[
        "hint", "explain", "example", "steps", "worked_example", "checklist", "reflection"
    ]
    session_id: str = Field(default="", max_length=200)
    request_id: str = Field(min_length=1, max_length=100)


@router.post("/topics/{path_id}/wenjin/help-requests")
async def record_learning_help_request(path_id: str, body: LearningHelpRequest):
    store = LearningStore()
    if not store.exists(path_id):
        raise HTTPException(status_code=404, detail="Learning project not found")
    if body.session_id and store.path_id_for_session(body.session_id) != path_id:
        raise HTTPException(status_code=403, detail="Session does not belong to this project")

    def record(tx):
        pending = tx.progress.pending_question
        tx.emit(
            "scaffold.requested",
            {
                "request_id": body.request_id,
                "kind": body.kind,
                "question_id": pending.question_id if pending else None,
                "objective_id": pending.knowledge_point_id if pending else None,
                "delivery_status": "requested_not_delivered",
            },
            session_id=body.session_id,
        )

    updated, _ = await asyncio.to_thread(store.mutate, path_id, record)
    return {"request_id": body.request_id, "revision": updated.version}


@router.post("/topics/{path_id}/diagnosis/training/jobs/{job_id}/retry")
async def retry_personal_training_job(path_id: str, job_id: str):
    from deeptutor.learning.training_jobs import retry_job

    return await _dataset_call(path_id, retry_job, job_id)


class ClassroomCreateRequest(BaseModel):
    mode: Literal["curriculum_ppt", "legacy"] = "curriculum_ppt"
    module_id: str = Field(default="", max_length=120)
    objective_ids: list[str] = Field(default_factory=list, max_length=30)
    minutes: int = Field(default=15, ge=5, le=60)


class ClassroomControlRequest(BaseModel):
    action: Literal["position", "retry", "cancel", "finish", "skip", "resume", "step", "view"]
    position: int | None = Field(default=None, ge=0)


class ClassroomAnswerRequest(BaseModel):
    index: int = Field(ge=0)
    choice: int = Field(ge=0, le=5)


class ClassroomAskRequest(BaseModel):
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=1, max_length=100)


@router.get("/topics/{path_id}/classrooms")
async def list_classrooms(path_id: str):
    from deeptutor.learning.classroom import listing

    return await _dataset_call(path_id, listing)


@router.post("/topics/{path_id}/classrooms")
async def create_classroom(path_id: str, body: ClassroomCreateRequest):
    from deeptutor.learning.classroom import create

    return await _dataset_call(
        path_id,
        create,
        body.objective_ids,
        body.minutes,
        body.mode == "curriculum_ppt",
        body.module_id,
    )


@router.get("/topics/{path_id}/classrooms/{cid}")
async def get_classroom(path_id: str, cid: str):
    from deeptutor.learning.classroom import load

    return await _dataset_call(path_id, load, cid)


@router.delete("/topics/{path_id}/classrooms/{cid}")
async def delete_classroom(path_id: str, cid: str):
    from deeptutor.learning.classroom import remove

    return await _dataset_call(path_id, remove, cid)


@router.post("/topics/{path_id}/classrooms/{cid}/confirm")
async def confirm_classroom(path_id: str, cid: str, body: dict):
    from deeptutor.learning.classroom import Outline, confirm

    try:
        sections = Outline.model_validate(body).sections
    except ValueError:
        raise HTTPException(422, "课堂大纲格式不正确") from None
    return await _dataset_call(path_id, confirm, cid, sections)


@router.post("/topics/{path_id}/classrooms/{cid}/control")
async def control_classroom(path_id: str, cid: str, body: ClassroomControlRequest):
    from deeptutor.learning.classroom import control

    return await _dataset_call(path_id, control, cid, body.action, body.position)


@router.post("/topics/{path_id}/classrooms/{cid}/answer")
async def answer_classroom(path_id: str, cid: str, body: ClassroomAnswerRequest):
    from deeptutor.learning.classroom import answer

    return await _dataset_call(path_id, answer, cid, body.index, body.choice)


@router.post("/topics/{path_id}/classrooms/{cid}/ask")
async def ask_classroom(path_id: str, cid: str, body: ClassroomAskRequest):
    from deeptutor.learning.classroom import ask

    try:
        return await ask(LearningStore(), path_id, cid, body.index, body.text, body.request_id)
    except LookupError:
        raise HTTPException(404, "课堂不存在") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(503, "课堂提问暂未完成，请稍后重试") from None


class ClassroomAssetRequest(BaseModel):
    kind: Literal["voice", "pdf", "pptx", "video", "simulation", "linear"]
    index: int = Field(default=0, ge=0)
    range: Literal["page", "chapter", "course"] | None = None
    engine: Literal["local", "configured"] = "local"
    rate: float = Field(default=1, ge=0.75, le=1.5)


class ClassroomAssetControlRequest(BaseModel):
    action: Literal["cancel", "retry", "playback"]
    seconds: float = Field(default=0, ge=0)
    playback_rate: float = Field(default=1, ge=0.5, le=2)


@router.get("/classroom-media/capabilities")
async def classroom_media_capabilities():
    from deeptutor.learning.classroom_av import capabilities

    return await asyncio.to_thread(capabilities)


@router.post("/topics/{path_id}/classrooms/{cid}/assets/{key}/control")
async def control_classroom_asset(
    path_id: str, cid: str, key: str, body: ClassroomAssetControlRequest
):
    from deeptutor.learning.classroom_media import control

    return await _dataset_call(
        path_id, control, cid, key, body.action, body.seconds, body.playback_rate
    )


class ClassroomSimulationRequest(BaseModel):
    phase: Literal["predict", "operate", "explain"]
    text: str = Field(default="", max_length=3000)
    choices: list[int] = Field(default_factory=list, max_length=8)


@router.post("/topics/{path_id}/classrooms/{cid}/assets")
async def request_classroom_asset(path_id: str, cid: str, body: ClassroomAssetRequest):
    from deeptutor.learning.classroom_media import request

    return await _dataset_call(
        path_id, request, cid, body.kind, body.index, body.range, body.engine, body.rate
    )


@router.get("/topics/{path_id}/classrooms/{cid}/assets/{key}")
async def get_classroom_asset(path_id: str, cid: str, key: str):
    from deeptutor.learning.classroom_media import get, view

    return view(await _dataset_call(path_id, get, cid, key))


@router.get("/topics/{path_id}/classrooms/{cid}/assets/{key}/files/{name}")
async def download_classroom_asset(path_id: str, cid: str, key: str, name: str):
    from fastapi.responses import FileResponse

    from deeptutor.learning.classroom_media import resource

    path = await _dataset_call(path_id, resource, cid, key, name)
    mime = {
        ".txt": "text/plain; charset=utf-8",
        ".json": "application/json",
        ".wav": "audio/wav",
        ".mp4": "video/mp4",
        ".vtt": "text/vtt",
        ".pdf": "application/pdf",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    return FileResponse(
        path, media_type=mime.get(path.suffix, "application/octet-stream"), filename=path.name
    )


@router.post("/topics/{path_id}/classrooms/{cid}/assets/{key}/interact")
async def classroom_simulation(path_id: str, cid: str, key: str, body: ClassroomSimulationRequest):
    from deeptutor.learning.classroom_media import interact

    if body.phase in {"predict", "explain"} and not body.text.strip():
        raise HTTPException(422, "请填写预测或解释")
    return await _dataset_call(path_id, interact, cid, key, body.phase, body.text, body.choices)


@router.get("/topics/{path_id}/classrooms/{cid}/assets")
async def list_classroom_assets(path_id: str, cid: str):
    from deeptutor.learning.classroom_media import listing

    return await _dataset_call(path_id, listing, cid)


class SlideTutorRequest(BaseModel):
    index: int = Field(ge=0)
    intent: Literal["explain", "check"] = "explain"


@router.post("/topics/{path_id}/classrooms/{cid}/tutor")
async def slide_tutor_handoff(path_id: str, cid: str, body: SlideTutorRequest):
    from deeptutor.learning.classroom import tutor_handoff

    return await _dataset_call(path_id, tutor_handoff, cid, body.index, body.intent)


class IndependentCheckStart(BaseModel):
    objective_id: str = Field(min_length=1, max_length=200)


@router.get("/topics/{path_id}/checks")
async def independent_checks(path_id: str):
    from deeptutor.learning.check_cycles import snapshot

    return await _dataset_call(path_id, snapshot)


@router.post("/topics/{path_id}/checks/start")
async def start_independent_check(path_id: str, body: IndependentCheckStart):
    from deeptutor.learning.check_cycles import start

    return await _dataset_call(path_id, start, body.objective_id)


class TeachAICreate(BaseModel):
    objective_id: str = Field(min_length=1, max_length=200)
    persona: Literal["curious", "confused"] = "curious"
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class TeachAITurn(BaseModel):
    request_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["explain", "hint", "example"] = "explain"
    text: str = Field(min_length=1, max_length=6000)


class TeachAIControl(BaseModel):
    action: Literal["pause", "resume", "finish"]


@router.get("/topics/{path_id}/teach-ai")
async def list_teach_ai(path_id: str):
    from deeptutor.learning.teach_ai import listing

    return await _dataset_call(path_id, listing)


@router.post("/topics/{path_id}/teach-ai")
async def create_teach_ai(path_id: str, body: TeachAICreate):
    from deeptutor.learning.teach_ai import create

    return await _dataset_call(path_id, create, body.objective_id, body.persona, body.request_id)


@router.get("/topics/{path_id}/teach-ai/{sid}")
async def load_teach_ai(path_id: str, sid: str):
    from deeptutor.learning.teach_ai import load

    return await _dataset_call(path_id, load, sid)


@router.post("/topics/{path_id}/teach-ai/{sid}/turn")
async def turn_teach_ai(path_id: str, sid: str, body: TeachAITurn):
    from deeptutor.learning.teach_ai import ModelUnavailable, turn

    try:
        return await turn(LearningStore(), path_id, sid, body.request_id, body.kind, body.text)
    except ModelUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/topics/{path_id}/teach-ai/{sid}/control")
async def control_teach_ai(path_id: str, sid: str, body: TeachAIControl):
    from deeptutor.learning.teach_ai import control

    return await _dataset_call(path_id, control, sid, body.action)


class LearningPortraitUpdate(BaseModel):
    source_revision: int = Field(ge=0)
    fields: dict[str, str] = Field(max_length=4)


class SupportChoice(BaseModel):
    objective_id: str = Field(min_length=1, max_length=200)
    action: Literal["restore_support", "adaptive", "independent"]


@router.get("/topics/{path_id}/learning-support")
async def learning_portrait(path_id: str):
    from deeptutor.learning.learning_support import portrait

    return await _dataset_call(path_id, portrait)


@router.put("/topics/{path_id}/learning-support/profile")
async def edit_learning_portrait(path_id: str, body: LearningPortraitUpdate):
    from deeptutor.learning.learning_support import edit_profile

    return await _dataset_call(path_id, edit_profile, body.fields, body.source_revision)


@router.post("/topics/{path_id}/learning-support/choice")
async def learning_support_choice(path_id: str, body: SupportChoice):
    from deeptutor.learning.learning_support import record_plan

    reasons = {
        "restore_support": "我希望多给一些学习支持，可随时恢复帮助。",
        "adaptive": "我选择由津津结合表现建议支持方式。",
        "independent": "我选择先独立试试，仍可随时求助；这不表示已掌握。",
    }
    return await _dataset_call(
        path_id,
        lambda store, project: record_plan(
            store,
            project,
            body.objective_id,
            body.action,
            reasons[body.action],
            [],
            learner_choice=True,
        ),
    )


class ClassroomEditSave(BaseModel):
    model_config = {"extra": "forbid"}
    revision: int = Field(ge=1)
    page_id: str = Field(min_length=1, max_length=120)
    changes: dict


class ClassroomEditPublish(BaseModel):
    model_config = {"extra": "forbid"}
    revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class ClassroomEditDiscard(BaseModel):
    model_config = {"extra": "forbid"}
    revision: int = Field(ge=1)


@router.post("/topics/{path_id}/classrooms/{cid}/editor")
async def start_classroom_edit(path_id: str, cid: str):
    from deeptutor.learning.classroom_editing import start

    return await _dataset_call(path_id, start, cid)


@router.get("/topics/{path_id}/classrooms/{cid}/editor/{did}")
async def get_classroom_edit(path_id: str, cid: str, did: str):
    from deeptutor.learning.classroom_editing import get

    return await _dataset_call(path_id, get, cid, did)


@router.put("/topics/{path_id}/classrooms/{cid}/editor/{did}")
async def save_classroom_edit(path_id: str, cid: str, did: str, body: ClassroomEditSave):
    from deeptutor.learning.classroom_editing import save_page

    return await _dataset_call(
        path_id, save_page, cid, did, body.revision, body.page_id, body.changes
    )


@router.post("/topics/{path_id}/classrooms/{cid}/editor/{did}/publish")
async def publish_classroom_edit(path_id: str, cid: str, did: str, body: ClassroomEditPublish):
    from deeptutor.learning.classroom_editing import publish

    return await _dataset_call(path_id, publish, cid, did, body.revision, body.reason)


@router.post("/topics/{path_id}/classrooms/{cid}/editor/{did}/discard")
async def discard_classroom_edit(path_id: str, cid: str, did: str, body: ClassroomEditDiscard):
    from deeptutor.learning.classroom_editing import discard

    return await _dataset_call(path_id, discard, cid, did, body.revision)


@router.get("/topics/{path_id}/classrooms/{cid}/editions")
async def classroom_editions(path_id: str, cid: str):
    from deeptutor.learning.classroom_editing import editions

    return await _dataset_call(path_id, editions, cid)


class ClassroomEditArrange(BaseModel):
    model_config = {"extra": "forbid"}
    revision: int = Field(ge=1)
    action: Literal["duplicate", "delete", "move"]
    page_id: str = Field(min_length=1, max_length=120)
    position: int | None = Field(default=None, ge=0)


@router.post("/topics/{path_id}/classrooms/{cid}/editor/{did}/pages")
async def arrange_classroom_edit(path_id: str, cid: str, did: str, body: ClassroomEditArrange):
    from deeptutor.learning.classroom_editing import arrange

    return await _dataset_call(
        path_id, arrange, cid, did, body.revision, body.action, body.page_id, body.position
    )


class GoalConfirmationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    source_revision: int = Field(ge=0)
    level: Literal["understand", "apply", "transfer"]
    summary: str = Field(min_length=1, max_length=1500)
    time_budget: str | None = Field(default=None, max_length=2000)
    reason: str = Field(default="", max_length=600)
    request_id: str = Field(min_length=8, max_length=128)


@router.get("/topics/{path_id}/goal-contract")
async def get_goal_contract(path_id: str):
    from deeptutor.learning.goal_contract import load

    return await _dataset_call(path_id, load)


@router.post("/topics/{path_id}/goal-contract")
async def confirm_goal_contract(path_id: str, body: GoalConfirmationRequest):
    from deeptutor.learning.goal_contract import confirm

    return await _dataset_call(
        path_id,
        lambda store, project: confirm(
            store,
            project,
            revision=body.source_revision,
            level=body.level,
            summary=body.summary,
            time_budget=body.time_budget,
            reason=body.reason,
            request_id=body.request_id,
        ),
    )
