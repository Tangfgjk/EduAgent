"""Conservative goal-to-catalog check for the local demonstration curriculum.

This is not semantic course generation. Unknown free-text goals must not silently
receive the bundled equation path.
"""
from __future__ import annotations

import unicodedata

from app.learning.assets import AssetCatalog


_DEMO_ALIASES = {
    "MATH.G7.EQ.": ("方程", "等式"),
}


def goal_target_kcs(goal_text: str, catalog: AssetCatalog) -> tuple[str, ...]:
    """Return catalog KCs relevant to a goal, including their prerequisites.

    This intentionally uses only reviewed catalog titles and explicit demo aliases.
    It does not infer a new curriculum from free text.
    """
    goal = unicodedata.normalize("NFKC", goal_text).replace(" ", "").strip()
    if not goal:
        return ()
    matched: set[str] = set()
    for asset in catalog.knowledge:
        title = unicodedata.normalize("NFKC", asset.title).replace(" ", "")
        if title and title in goal:
            matched.add(asset.ref.asset_id)
    if not matched:
        for asset in catalog.knowledge:
            for prefix, aliases in _DEMO_ALIASES.items():
                if asset.ref.asset_id.startswith(prefix) and any(alias in goal for alias in aliases):
                    matched.add(asset.ref.asset_id)
    by_key = {asset.ref.asset_id: asset for asset in catalog.knowledge}
    selected: set[str] = set()

    def include(kc_id: str) -> None:
        if kc_id in selected:
            return
        selected.add(kc_id)
        for prerequisite in by_key[kc_id].prerequisite_refs:
            include(prerequisite.asset_id)

    for kc_id in matched:
        include(kc_id)
    return tuple(asset.ref.asset_id for asset in catalog.knowledge if asset.ref.asset_id in selected)


def goal_supported(goal_text: str, catalog: AssetCatalog) -> bool:
    return bool(goal_target_kcs(goal_text, catalog))


def unsupported_goal_message(goal_text: str, catalog: AssetCatalog) -> str:
    titles = "、".join(asset.title for asset in catalog.knowledge)
    return f"当前课程不支持目标“{goal_text[:80]}”。可用主题：{titles}。请先选择现有课程；新主题需要导入并审核课程资产。"


def plan_has_curriculum_scope(content: dict) -> bool:
    """Whether a plan claims concrete course knowledge or assessments."""
    path = content.get("path") or {}
    return bool(content.get("bank_scope") or content.get("kc_refs") or
                (isinstance(path, dict) and path.get("nodes")))
