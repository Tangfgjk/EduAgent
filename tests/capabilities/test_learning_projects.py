"""Project uploads, generation and progress recovery preserve learning invariants."""

import json
from types import SimpleNamespace

import pytest

from deeptutor.learning import projects
from deeptutor.learning.models import (
    KnowledgePoint,
    LearningModule,
    LearningProgress,
    TopicMetadata,
)
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.topic_materials import TopicMaterials, build_topic_materials


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "upload_root", lambda: tmp_path / "uploads")
    result = LearningStore(tmp_path / "store")
    result.save(
        LearningProgress(
            book_id="p",
            modules=[
                LearningModule(
                    id="m",
                    name="Equations",
                    order=0,
                    objective="Understand equations",
                    knowledge_points=[
                        KnowledgePoint(
                            id="k", name="Identify equations", type="concept", module_id="m"
                        )
                    ],
                )
            ],
        )
    )
    result.put_topic(TopicMetadata(path_id="p", goal="Learn equations"), [])
    return result


@pytest.mark.asyncio
async def test_upload_is_parsed_deduplicated_and_readable_to_tutor(store):
    source = await projects.import_file("lesson.txt", b"An equation expresses equality.")
    duplicate = await projects.import_file("duplicate.txt", b"An equation expresses equality.")
    assert source.source_id == duplicate.source_id
    projects.attach_upload(store, "p", source.source_id)
    projects.attach_upload(store, "p", source.source_id)
    assert len(store.get_topic("p").sources) == 1
    manifest = build_topic_materials(store.get_topic("p").sources)
    assert (
        manifest.source_index()["project_file:" + source.source_id]
        == "An equation expresses equality."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename,data", [("empty.txt", b""), ("script.exe", b"bad"), ("bad.pdf", b"invalid")]
)
async def test_bad_upload_is_clear_error(store, filename, data):
    with pytest.raises(ValueError):
        await projects.import_file(filename, data)


def test_traversal_and_missing_upload_are_rejected(store):
    for sid in ("../private", "a" * 32):
        with pytest.raises(ValueError):
            projects.read_upload(sid)


@pytest.mark.asyncio
async def test_client_cannot_forge_uploaded_text(store):
    from fastapi import HTTPException

    from deeptutor.api.routers.mastery_path import TopicSourceRequest, _topic_sources

    source = await projects.import_file("lesson.md", b"Real text")
    canonical = _topic_sources(
        [
            TopicSourceRequest(
                kind="project_file",
                source_id=source.source_id,
                label="fake title",
                excerpt="forged facts",
            )
        ]
    )[0]
    assert canonical.excerpt == "Real text"
    assert canonical.label == "lesson.md"
    with pytest.raises(HTTPException):
        _topic_sources([TopicSourceRequest(kind="project_file", source_id="f" * 32, label="fake")])


@pytest.mark.asyncio
async def test_topic_only_generation_cached_and_not_mastery(store, monkeypatch):
    calls = []

    async def complete(**kwargs):
        calls.append(json.loads(kwargs["messages"][1]["content"]))
        return "# Chapter\nAn equation expresses equality."

    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: SimpleNamespace())
    monkeypatch.setattr("deeptutor.services.llm.factory.complete_with_config", complete)
    first = await projects.generate_chapter(store, "p", "m")
    projects.save_location(store, "p", "m", 0.7)
    second = await projects.generate_chapter(store, "p", "m")
    assert first == second
    assert len(calls) == 1
    assert calls[0]["materials"] == []
    state = store.load("p")
    assert not state.mastery_levels and not state.quiz_attempts
    assert state.learning_location["position"] == 0.7
    materials = TopicMaterials()
    projects.append_chapter_materials(materials, state)
    assert materials.source_index()["chapter:m"] == first["content"]


@pytest.mark.asyncio
async def test_generation_rejects_outline_edited_while_model_runs(store, monkeypatch):
    async def complete(**kwargs):
        def edit(tx):
            tx.progress.modules[0].objective = "New objective"
            tx.touch()

        store.mutate("p", edit)
        return "Obsolete chapter"

    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: SimpleNamespace())
    monkeypatch.setattr("deeptutor.services.llm.factory.complete_with_config", complete)
    with pytest.raises(ValueError, match="outline changed"):
        await projects.generate_chapter(store, "p", "m")
    assert not store.load("p").chapter_materials


@pytest.mark.asyncio
async def test_generation_rejects_goal_changed_during_model_call(store, monkeypatch):
    async def complete(**kwargs):
        store.put_topic(TopicMetadata(path_id="p", goal="Different goal"), [])
        return "Obsolete chapter"

    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: SimpleNamespace())
    monkeypatch.setattr("deeptutor.services.llm.factory.complete_with_config", complete)
    with pytest.raises(ValueError, match="goal changed"):
        await projects.generate_chapter(store, "p", "m")
    assert not store.load("p").chapter_materials


def test_location_validated_and_independent_of_mastery(store):
    projects.save_location(store, "p", "m", 0.4, "tutoring")
    state = store.load("p")
    assert state.learning_location["surface"] == "tutoring"
    assert not state.mastery_levels
    with pytest.raises(ValueError):
        projects.save_location(store, "p", "gone", 0.5)
    assert store.load("p").learning_location["chapter_id"] == "m"
