"""自编、版本化的开发样例。没有教师金标；仅模拟/成人测试可启用。"""
import hashlib
import json

UNIT_ID = "math.g7.equations.dev1"
TITLE = "一元一次方程与应用 · 开发单元"
TASKS = [
    {"id": "D-EQ-01", "stage": "diagnostic", "stem": "解方程：3x + 5 = 14。请给出 x 的值，并写下你的想法。", "answer": "3", "format": "填写数值或 x=数值，可使用分数与四则运算。", "hints": []},
    {"id": "P-EQ-01", "stage": "practice", "stem": "解方程：2x − 7 = 5。每次变形，等式两边做了什么？", "answer": "6", "format": "填写 x 的值，步骤写在下方。", "hints": ["想让含 x 的项单独留在一边，先观察常数项。", "怎样在等式两边做相同的运算，消去左边的 −7？", "两边同时加 7，得到 2x = 12；下一步怎样让 x 的系数变成 1？", "两边同时加 7：2x = 12；再同时除以 2：x = 6。代入检查：2×6−7=5。请用自己的话复述依据。"]},
    {"id": "P-APP-01", "stage": "practice", "stem": "每支笔 3 元，另付一次包装费 5 元，一共付了 26 元。买了几支笔？请设未知数列方程，并给出支数。", "answer": "7", "format": "答案只填支数（数值），列式和依据写在下方。", "hints": ["总费用由哪两部分组成？包装费要付几次？", "设买了 x 支笔，把买笔的费用与包装费相加。", "方程是 3x + 5 = 26。先用等式性质处理常数项，继续算一算。", "设买了 x 支笔，3x+5=26，两边减 5 得 3x=21，再除以 3 得 x=7。检查：3×7+5=26。注意包装费只付一次。"]},
    {"id": "C-EQ-01", "stage": "checkpoint", "stem": "独立检测 1：解方程 4x + 3 = 23，求 x。请独立完成。", "answer": "5", "format": "填写 x 的值。本次检测提交后不能修改，不提供提示。", "hints": []},
    {"id": "C-APP-01", "stage": "checkpoint", "stem": "独立检测 2：每本练习本 6 元，另付一次包装费 4 元，总共付了 40 元。买了几本？请列式并给出本数。", "answer": "6", "format": "答案只填本数（数值），列式写在下方；提交后不能修改。", "hints": []},
]
CONTENT_VERSION = hashlib.sha256(json.dumps(TASKS, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def task_public(index):
    item = TASKS[index]
    return {"task_id": item["id"], "stem": item["stem"], "answer_format": item["format"]}
