"""独立审核的开发互动模板；不是模型生成，也不是新增测评题库。"""
from hashlib import sha256
import json

from app.modules.learning.development_content import TASKS

UNIT_ID = "math.g7.equations.activities.dev1"
TITLE = "方程圆桌与我教 AI · 开发活动"
TASK = next(task for task in TASKS if task["id"] == "P-EQ-01")
ROLES = [
    {"speaker": "思路提出者", "text": "我建议把等式两边同时加上 7，先消去左边的常数项。", "category": "role_opinion"},
    {"speaker": "质疑者", "text": "另一位学伴说：把 −7 移到右边仍写成 −7，因此 2x=5−7。这一步成立吗？请代入核验。", "category": "role_opinion"},
    {"speaker": "整理者", "text": "请比较两种说法，说明等式两边实际进行了什么运算，再给出可检验的数值。", "category": "role_opinion"},
]
MISCONCEPTION = {"speaker": "虚拟学伴", "text": "我把 −7 移到右边后仍写成 −7，得到 2x=5−7。请你指出这一步的问题，并教我怎样代入检查。", "category": "preset_misconception"}
TEMPLATES = {"roundtable": ROLES, "teach": [MISCONCEPTION]}
CONTENT_VERSION = sha256(json.dumps({"task": TASK, "templates": TEMPLATES, "flow": "student-first.v1"}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def admin_tasks():
    return [{**TASK, "activity_templates": TEMPLATES,
             "boundary": "模板角色并不读取或评价学生文字；仅在最后核对数值，解释质量尚未评定。"}]
