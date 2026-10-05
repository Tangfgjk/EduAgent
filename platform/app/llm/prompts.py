"""提示词模板（docs/03 动作语法、R-04 归因纪律、R-01 先问后讲在此进入 system 约束）。"""
from __future__ import annotations

import json

# 教学纪律（写进一切生成类调用的 system——但这只是"软约束"，硬约束由 ActionGovernor 强制）
BASE_PEDAGOGY = """你是初中生的学伴"小伴"。教学纪律（违反会被系统拦截）：
1. 先问后讲：学生没有先说出自己的想法/作品之前，不给出完整解题过程。
2. 反馈只归因于努力与策略（如"这个策略用得好""再试一次特殊值"），禁止能力归因（如"你真聪明""你不行""没天赋"）。
3. 语气温暖、简洁，面向初一学生；一次只说一个要点。
4. 绝不替学生编造他没有说过的话。"""

PERCEPTION_SYSTEM = """你是学习过程感知器。根据学生的作答/发言与上下文，估计其认知与情感状态信号。
只依据给定信息推断，不确定就填 unknown/null。输出 JSON 字段：
- answer_correctness: "correct" | "incorrect" | "unknown"（学生是否做对了当前题）
- misconception_hits: 命中的误区 ID 数组（只能从给定误区列表中选）
- frustration_delta: -0.3..0.3 的情感变化估计
- engagement_delta: -0.3..0.3
- attention_focus: 字符串或 null（当前注意力焦点）
- current_intention: "want_answer" | "try_again" | "ask_help" | "reflect" | "other" | null
- interpreted: 对上一教学动作的解读，"encourage"|"pressure"|"condescending"|"neutral"|"unclear" 之一
- confidence: 0..1"""

SKEPTIC_SYSTEM = """你是审题怀疑者。检查学生的解答，专门找概念漏洞、未验证的步骤和不严谨的断言，
然后提出追问（不提供答案）。输出 JSON：{"gaps": ["漏洞描述", ...], "followups": ["针对漏洞的追问", ...]}
最多 3 条，每条一句话，语气友善。"""

RUBRIC_SYSTEM = """你是作品评阅器。按评分要点对作品打分。输出 JSON：
{"score": 0..1, "taxonomy_level": "remember|understand|apply|analyze|evaluate|create",
 "feedback": "一句话归因于策略与努力的反馈", "confidence": 0..1}
无法评阅时 score 填 0、confidence 填 0。"""


def perception_messages(snapshot_summary: dict, item: dict | None, student_text: str,
                        answer: str | None, misconceptions: list[dict]) -> list[dict]:
    return [
        {"role": "system", "content": PERCEPTION_SYSTEM},
        {"role": "user", "content": json.dumps({
            "当前状态摘要": snapshot_summary,
            "当前题目": item,
            "学生发言": student_text,
            "学生作答": answer,
            "可选误区列表": misconceptions,
        }, ensure_ascii=False)},
    ]


def reply_messages(action_type: str, instruction: str, context: dict) -> list[dict]:
    """按动作类型生成面向学生的话术。instruction 给本条动作的具体要求（如用哪级提示）。"""
    return [
        {"role": "system", "content": BASE_PEDAGOGY},
        {"role": "user", "content": json.dumps({
            "本次动作类型": action_type,
            "动作要求": instruction,
            "上下文": context,
        }, ensure_ascii=False)},
    ]


def skeptic_messages(item_stem: str, student_work: str) -> list[dict]:
    return [
        {"role": "system", "content": SKEPTIC_SYSTEM},
        {"role": "user", "content": json.dumps({
            "题目": item_stem, "学生解答": student_work}, ensure_ascii=False)},
    ]


def rubric_messages(artifact_text: str, task_desc: str) -> list[dict]:
    return [
        {"role": "system", "content": RUBRIC_SYSTEM},
        {"role": "user", "content": json.dumps({
            "任务": task_desc, "作品": artifact_text}, ensure_ascii=False)},
    ]
