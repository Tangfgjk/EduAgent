"""Reproducible AI draft builder. Output is pending and restricted to simulation."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.learning.assets import AssetCatalog, VersionedRef, load_catalog
from app.learning.course_governance import (
    CourseReviewPackage, CourseSource, HintStep, KnowledgeReview, MeasurementWindow,
    MisconceptionReview, RubricReview, TaskReview,
)


def build_equation_course_draft() -> CourseReviewPackage:
    """Freeze 24 easy tasks across two prerequisite KCs, six phases and two items.

    Difficulty, transfer and misconception descriptions remain hypotheses for a
    teacher to review; freezing them does not establish empirical comparability.
    """
    from app.config import Settings
    catalog = load_catalog(Settings())
    kc_ids = {"MATH.G7.EQ.BALANCE", "MATH.G7.EQ.SOLVE"}
    selected = tuple(asset for asset in catalog.assessments if asset.ref.asset_id.startswith("SYN-")
                     and asset.difficulty_band == "easy" and all(ref.asset_id in kc_ids for ref in asset.kc_refs))
    rubric_keys = {asset.rubric_ref.key for asset in selected}
    snapshot = AssetCatalog(catalog_version="v3-equation-course-draft-20261006",
        knowledge=tuple(kc for kc in catalog.knowledge if kc.ref.asset_id in kc_ids),
        rubrics=tuple(rubric for rubric in catalog.rubrics if rubric.ref.key in rubric_keys),
        assessments=selected)
    source = Path(__file__).resolve().parents[2] / "seeds" / "synthetic" / "learning_assets_overlay_v1.json"
    knowledge_reviews = []
    misconceptions = []
    for kc in snapshot.knowledge:
        balance = kc.ref.asset_id.endswith("BALANCE")
        knowledge_reviews.append(KnowledgeReview(kc_ref=kc.ref,
            observable_objective=("能在等式两边执行同一非零可逆运算，并通过代入检验说明仍然相等。" if balance
                                  else "能按等式性质独立求出未知数，记录逆运算并代回原式自检。"),
            positive_example="对 x+3=8 两边同时减3，并将所得值代入左右两边核对。",
            negative_example="只修改等号左边并认为等式仍成立；不能说明为什么两边相等。"))
        misconceptions.append(MisconceptionReview(misconception_id=f"{kc.ref.asset_id}.one_sided_operation",
            kc_refs=(kc.ref,), description="只对等式一边运算，或移项时遗漏逆运算。",
            positive_error_example="x+3=8 被错误写为 x=8+3，只改变了左边而未维护相等。",
            negative_error_example="x+3=8 两边同时减3，随后把结果代回原等式。",
            evidence_basis="synthetic misconception hypothesis; not observed human frequency or teacher gold",
            intervention="要求学生指出左右两边各执行了什么运算，使用代入验证纠正，而不直接给最终答案。"))
    task_reviews = []
    for asset in snapshot.assessments:
        kc_id = asset.kc_refs[0].asset_id
        task_reviews.append(TaskReview(assessment_ref=asset.ref,
            difficulty_basis="草案容易层：一个未知数、整数常数和最多两步逆运算；未经真人难度校准。",
            bloom_requirement=("学生应解释两边同运算为何保持等式，并通过代入证明结果；不是认知能力诊断。"
                               if asset.bloom_level == "understand" else
                               "学生应独立执行逆运算并代回原式核验；任务要求标签不是学生认知能力结论。"),
            misconception_refs=(f"{kc_id}.one_sided_operation",),
            hints=(HintStep(level=0, text="先独立作答，并保留你的推导和自检过程。"),
                   HintStep(level=1, text="指出等号两边的量，考虑怎样同时执行逆运算以保持相等。"),
                   HintStep(level=2, text="先去掉常数项，再考虑未知数的系数；每一步都对两边执行同一运算。")),
            fade_condition="连续两次无提示正确且有代入自检后，下次先降一级支架；遇错再根据误区补充。",
            transfer_difference=("从符号方程转为文字数量关系，先建模再逆运算；属于近迁移假设，非远迁移证明。"
                                 if asset.kind == "transfer" else None)))
    return CourseReviewPackage(course_ref=VersionedRef(asset_id="math-g7-linear-equations", version="3.1.0-draft"),
        title="初中一元一次方程：AI候选课程审核包（待教师审核）",
        created_at=datetime(2026, 10, 6, 17, 33, tzinfo=timezone(timedelta(hours=8))), review_status="pending",
        source=CourseSource(source_kind="synthetic_ai_generated",
            locator="seeds/synthetic/learning_assets_overlay_v1.json; KC references: seeds/learning_assets_v1.json",
            # The frozen review package hashes the repository's LF form. Git may
            # check this JSON out with CRLF on Windows; normalize line endings so
            # the candidate identity is reproducible across developer machines.
            source_sha256=hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            license_ref="local-development-synthetic-candidate; no externally licensed curriculum claimed",
            usage_scope="simulation_only", grade_band="G7"), catalog=snapshot,
        knowledge_reviews=tuple(knowledge_reviews), task_reviews=tuple(task_reviews),
        rubric_reviews=tuple(RubricReview(rubric_ref=rubric.ref,
            level_descriptions=("0：答案与可验证条件不等价或无法完成。", "1：答案等价；推导质量另由教师复核。"),
            positive_example="正确解答并代回原等式两边得到相同结果。",
            negative_example="只给错误的数值，或者使用不受支持的表达式无法验算。",
            scoring_procedure="使用受限数学验证器检查答案等价性；不可验证不计入掌握与正式测量。",
            disagreement_procedure="记录争议证据和版本，请真实教师复核；不以LLM自评直接改写学习证据。")
            for rubric in snapshot.rubrics), misconceptions=tuple(misconceptions),
        measurement_windows=(MeasurementWindow(phase="pretest", start_offset_minutes=-60, end_offset_minutes=0),
            MeasurementWindow(phase="posttest", start_offset_minutes=0, end_offset_minutes=1440),
            MeasurementWindow(phase="transfer", start_offset_minutes=1440, end_offset_minutes=2880),
            MeasurementWindow(phase="delayed", start_offset_minutes=10080, end_offset_minutes=20160)))
