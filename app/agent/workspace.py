"""学习工作区（ZCode 的"仓库"对应物）：真实磁盘文件，智能体可读写。

布局：workspace/<learner>/
  计划.md        学习计划（关口①确认后生效）
  错题本.md      错题归档（含重练排期）
  笔记/          学生与智能体的笔记
  作品/          探究作品与代码
安全：禁止路径穿越；只允许白名单顶层文件与两个子目录。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

TOP_FILES = {"计划.md", "错题本.md"}
SUB_DIRS = {"笔记", "作品"}


class WorkspaceError(ValueError):
    pass


class LearningWorkspace:
    def __init__(self, root: str | Path, learner_id: str):
        self.root = Path(root) / learner_id
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUB_DIRS:
            (self.root / sub).mkdir(exist_ok=True)

    # ---------- 路径安全 ----------

    def resolve(self, name: str) -> Path:
        name = name.strip().strip("/")
        if ".." in name or name.startswith(".") or ":" in name:
            raise WorkspaceError(f"非法路径：{name}")
        top = name.split("/")[0]
        path = self.root / name
        if top in TOP_FILES:
            if Path(name).parent != Path("."):
                raise WorkspaceError(f"{top} 是单文件，不允许子路径")
            return path
        if top in SUB_DIRS:
            if len(name.split("/")) < 2:
                raise WorkspaceError(f"{top}/ 下需要给出文件名")
            return path
        raise WorkspaceError(f"只允许 {sorted(TOP_FILES)} 或 {sorted(SUB_DIRS)}/ 下的文件")

    # ---------- 通用读写 ----------

    def read(self, name: str) -> str:
        path = self.resolve(name)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write(self, name: str, content: str) -> str:
        path = self.resolve(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    # ---------- 结构化条目 ----------

    def write_plan(self, content_md: str) -> str:
        """计划.md（关口①的确认对象）。"""
        return self.write("计划.md", content_md)

    def append_mistake(self, item_id: str, stem: str, student_answer: str,
                       correct_answer: str, misconception: str,
                       retry_days: int = 1) -> str:
        """错题归档：题目/你的答案/正解/误区假设/重练排期（间隔重复的起点）。"""
        retry_on = (datetime.now() + timedelta(days=retry_days)).strftime("%Y-%m-%d")
        entry = (
            f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} · {item_id}\n"
            f"- 题目：{stem}\n"
            f"- 你的答案：{student_answer}\n"
            f"- 正解：{correct_answer}\n"
            f"- 误区假设：{misconception or '待诊断'}\n"
            f"- 重练排期：{retry_on}\n"
        )
        path = self.root / "错题本.md"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        return str(path)

    def plan_summary(self, content_md: str, max_lines: int = 6) -> str:
        """取计划正文的摘要（关口确认时展示）。"""
        lines = [ln for ln in content_md.splitlines() if ln.strip() and not ln.startswith("#")]
        return "；".join(ln.strip(" -") for ln in lines[:max_lines])


def default_plan_md(goal: str, daily_count: int = 2,
                    deadline_note: str = "") -> str:
    """计划模板（LLM 可润色，结构固定以便关口审阅）。"""
    lines = [
        f"# 学习计划：{goal}",
        "",
        f"- 节奏：每天 {daily_count} 题 + 每周 1 次阶段测试",
        "- 路线：解方程 → 设未知数/找等量关系 → 应用题综合",
        "- 提示阶梯随自主性渐撤；错题次日重练",
    ]
    if deadline_note:
        lines.insert(2, f"- 倒排：{deadline_note}")
    lines.append("")
    return "\n".join(lines)
