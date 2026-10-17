"""导出教师审核底稿；仅读取代码注册表，不连接数据库或模型。"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modules.learning.content_catalog import PACKS
from app.modules.learning.activity_catalog import ACTIVITY_PACKS


# 人工编写的开发复算依据，与注册表答案分别维护；不代表教师金标。
REFERENCES = {
    'D-EQ-01': ('(14-5)/3', '等式两边减 5，再除以 3；代回 3x+5=14。'),
    'P-EQ-01': ('(5+7)/2', '等式两边加 7，再除以 2；代回 2x−7=5。'),
    'P-APP-01': ('(26-5)/3', '总费用先扣除一次包装费，再除以每支价格；数量须为非负整数。'),
    'C-EQ-01': ('(23-3)/4', '等式两边减 3，再除以 4。'),
    'C-APP-01': ('(40-4)/6', '总费用扣除一次包装费，再除以每本价格。'),
    'D-RAT-01': ('-3+8', '异号相加，绝对值作差，结果取正号。'),
    'P-RAT-01': ('-6/3+4', '先除后加，−6÷3=−2。'),
    'P-RAT-02': ('-2+7-3', '温度变化依次记为 +7 和 −3。'),
    'C-RAT-01': ('-12/4+8', '先算 −12÷4，再加 8。'),
    'C-RAT-02': ('-4+9-2', '升温为正、降温为负；保留起始温度。'),
    'D-LIN-01': ('2*3+1', '将 x=3 代入给定函数。'),
    'P-LIN-01': ('-2*(-1)+5', '代入负数须保留括号，负负相乘为正。'),
    'P-LIN-02': ('3*5+4', '购买件数为 5，固定费用只计一次。'),
    'C-LIN-01': ('-3*(-2)+2', '将 x=−2 代入，先乘后加。'),
    'C-LIN-02': ('4*3+6', '给定费用模型代入 3 件。'),
    'D-PYT-01': ('5', '3²+4²=25=5²；边长取正。'),
    'P-PYT-01': ('10', '6²+8²=100=10²；边长取正。'),
    'P-PYT-02': ('12', '13²−5²=144=12²；未知量是直角边。'),
    'C-PYT-01': ('15', '9²+12²=225=15²；边长取正。'),
    'C-PYT-02': ('8', '10²−6²=64=8²；未知量是直角边。'),
    'D-PRB-01': ('1/6', '六个等可能点数中，只有 6 符合事件。'),
    'P-PRB-01': ('3/6', '有利结果为 {2,4,6}，样本空间为 {1,2,3,4,5,6}。'),
    'P-PRB-02': ('2/(3+2)', '按五个等可能的球计数，蓝球占两个；颜色种类并不等可能。'),
    'C-PRB-01': ('2/6', '大于 4 的点数为 {5,6}。'),
    'C-PRB-02': ('3/(4+3)', '七个等可能的球中有三个黑球。'),
    'D-PCO-01': ('1/4', '四张卡片等可能，只有编号 4 符合。'),
    'P-PCO-01': ('2/8', '1 至 8 的 3 的倍数为 {3,6}。'),
    'P-PCO-02': ('3/(2+3)', '仅红蓝两色，不是红球即蓝球；也可用 1−2/5。'),
    'C-PCO-01': ('3/10', '大于 7 的编号为 {8,9,10}。'),
    'C-PCO-02': ('1/6', '只有红白两色，不是白球即唯一的红球。'),
    'D-STA-01': ('(2+4+6)/3', '算术平均数为总和除以数据个数。'),
    'P-STA-01': ('6', '数据已排序，五项数据的第三项为 6；不是使用平均数公式。'),
    'P-STA-02': ('4/20', '本次实验频率为出现次数除以实验次数，不用理论概率替代。'),
    'C-STA-01': ('(3+5+7)/3', '算术平均数为总和除以数据个数。'),
    'C-STA-02': ('9/30', '只计算本次实验频率，不据此断言理论概率。'),
}


def build_packet():
    # 本地有界解析器只用于复算人工表达式，不读取或推断题干含义。
    from app.modules.learning.numeric_verifier import parse_number
    task_ids = [task['id'] for pack in PACKS for task in pack.tasks]
    if len(task_ids) != len(set(task_ids)) or set(task_ids) != set(REFERENCES):
        raise ValueError('审核复算依据必须与当前全部题目一一对应')
    units = []
    for pack in PACKS:
        tasks = []
        for task in pack.tasks:
            expression, rationale = REFERENCES[task['id']]
            calculated = parse_number(expression)
            tasks.append({**task, 'development_calculation': expression,
                'development_rationale': rationale, 'calculated_answer': str(calculated),
                'reference_matches': calculated == Fraction(task['answer']),
                'professional_review': 'pending', 'reviewer': None,
                'standard_mapping': None, 'textbook_mapping': None,
                'review_conclusion': None, 'review_note': None})
        units.append({'unit_id': pack.unit_id, 'title': pack.title,
            'content_version': pack.version, 'grade_suggestion': pack.grade,
            'knowledge_points': list(pack.knowledge_points), 'tasks': tasks})
    activities = [{'unit_id': a.unit_id, 'title': a.title, 'content_version': a.version,
        'parent_unit_id': a.parent_unit_id, 'parent_version': a.parent.version,
        'task_id': a.task['id'], 'flow': a.flow,
        'verification_task_id': a.verification_task['id'] if a.verification_task else None,
        'templates': {k: v for k, v in a.templates.items()
                      if k == 'teach' or a.flow != 'teach_cycle_v1'},
        'professional_review': 'pending', 'review_note': None} for a in ACTIVITY_PACKS]
    return {'schema_version': 1, 'purpose': 'teacher_review_draft',
        'contains_reference_answers': True, 'real_student_release': False,
        'limitations': '人工开发复算，不自动理解题意、评价提示质量、映射课标或评定教学效果；不写入审核或启用记录。',
        'task_count': len(task_ids),
        'reference_mismatch_count': sum(not t['reference_matches'] for u in units for t in u['tasks']),
        'units': units, 'activities': activities}


def render(packet, digest):
    lines = ['# 数学内容专业审核底稿', '',
        '> 含参考答案及末级提示，仅供内部审核；不要作为学生资料发放。', '',
        f'JSON 文件 SHA256：`{digest}`', '',
        f"覆盖 {len(packet['units'])} 个内容包、{packet['task_count']} 道题、{len(packet['activities'])} 个互动包。",
        f"开发复算与注册答案不一致：{packet['reference_mismatch_count']} 项。专业审核全部待完成。", '',
        packet['limitations'], '', '## 审核方法', '',
        '逐题复核题意与条件充分性、独立解答、答案与等价形式、提示梯度与泄题、误区措辞、年级和先修要求。',
        '课标映射须填写文件版本、条目及页码；教材映射须填写出版社、版次、年级册次与页码。无来源保持待确认。',
        '结论填写“可用 / 需修改 / 待确认”，附审核人、日期与依据。开发复算一致不等于专业审核通过。',
        '修订代码后版本变化，须重新导出底稿并复核；本文件不自动导入管理端或开放内容。', '',
        '重点：概率按等可能基本结果计数；频率不等于理论概率；几何计算不代表证明；数值正确不代表推理或素养达标。', '']
    for unit in packet['units']:
        lines += [f"## {unit['title']}", '', f"`{unit['unit_id']}` · 建议年级：{unit['grade_suggestion']}", '',
            f"版本：`{unit['content_version']}`", '', '知识点：' + '、'.join(unit['knowledge_points']), '']
        for task in unit['tasks']:
            lines += [f"### {task['id']} · {task['stage']}", '', task['stem'], '',
                '输入约束：' + task['format'], '', f"注册答案：`{task['answer']}`；开发复算：`{task['development_calculation']}` → `{task['calculated_answer']}`。", '',
                '开发依据：' + task['development_rationale'], '', '逐级提示：', '']
            lines += [f"{i}. {hint}" for i, hint in enumerate(task['hints'], 1)] if task['hints'] else ['无提示。']
            lines += ['', '已记录误区：' + (task.get('misconception') or '未单独标注，待审核补充。'), '',
                '- [ ] 题意与前提充分，已独立解答', '- [ ] 答案、等价形式和单位合适',
                '- [ ] 提示梯度、末级揭示和误区表述合适', '- [ ] 年级、先修关系及课标/教材来源已核对', '',
                '审核人 / 日期：待填写；结论：待审核；依据 / 修订建议：待填写。', '']
    lines += ['## 互动模板专项审核', '',
        '预设错误必须明确是待质疑的观点，不能作为正确知识展示；检查其后是否引导核验与纠错。',
        '我教 AI 的另一题验证仅无本轮帮助，可能此前见过；不直接认定教学、迁移或解释能力。', '']
    for activity in packet['activities']:
        lines += [f"### {activity['title']}", '', f"`{activity['unit_id']}`；版本 `{activity['content_version']}`", '',
            f"来源 `{activity['parent_unit_id']}` / `{activity['parent_version']}`；题目 `{activity['task_id']}`；流程 `{activity['flow']}`；验证题 `{activity['verification_task_id']}`。", '']
        for kind, templates in activity['templates'].items():
            lines += [f'**{kind}**', '']
            lines += [f"- {t['speaker']}（{t['category']}）：{t['text']}" for t in templates]
            lines += ['']
        lines += ['审核人 / 日期：待填写；结论：待审核；数学正确性 / 预设错误标识 / 纠错引导 / 修订建议：待填写。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path, help='创建新目录，拒绝覆盖已有目录')
    args = parser.parse_args()
    packet = build_packet()
    payload = (json.dumps(packet, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'review.json').write_bytes(payload)
    (args.output_dir / 'review.md').write_text(render(packet, hashlib.sha256(payload).hexdigest()), encoding='utf-8')
    print(json.dumps({'task_count': packet['task_count'], 'reference_mismatch_count': packet['reference_mismatch_count'],
        'professional_review': 'pending', 'model_calls': 0}, ensure_ascii=False))
    return 1 if packet['reference_mismatch_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
