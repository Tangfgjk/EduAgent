"""从开发注册表导出实现快照；不读取账号、数据库或外部资料。"""
import argparse
import json
from pathlib import Path

from app.modules.learning.activity_catalog import ACTIVITY_PACKS
from app.modules.learning.content_catalog import PACKS
from app.modules.learning.numeric_verifier import VERIFIER_VERSION


def inventory():
    for activity in ACTIVITY_PACKS:
        matches = [pack for pack in PACKS if pack.unit_id == activity.parent_unit_id
                   and any(task['id'] == activity.task['id'] for task in pack.tasks)]
        if len(matches) != 1:
            raise ValueError('互动题必须能唯一定位到基础内容包')
    supported = {activity.parent_unit_id for activity in ACTIVITY_PACKS}
    return {
        'schema_version': 2,
        'scope': 'source_code_inventory_not_runtime_approval_or_curriculum_completion',
        'limitations': '当前未接真实模型，不自动理解课程正文或评定教学效果。',
        'professional_review': 'pending',
        'curriculum_textbook_mapping': 'pending',
        'real_student_release': False,
        'unit_count': len(PACKS),
        'task_count': sum(len(pack.tasks) for pack in PACKS),
        'units': [{
            'unit_id': pack.unit_id,
            'title': pack.title,
            'grade_suggestion': pack.grade,
            'registry_domain': pack.domain,
            'content_version': pack.version,
            'knowledge_points': list(pack.knowledge_points),
            'prerequisite_references': list(pack.prerequisites),
            'coverage': 'partial_development_sample',
            'verifier': VERIFIER_VERSION,
            'tasks': [{'task_id': task['id'], 'stage': task['stage'],
                       'hint_count': len(task['hints']),
                       'misconception_recorded': bool(task.get('misconception'))} for task in pack.tasks],
            'numeric_tutoring_review_evidence': 'implemented_development_only',
            'roundtable': 'fixed_template' if pack.unit_id in supported else 'not_implemented',
            'teach_ai': 'fixed_template' if pack.unit_id in supported else 'not_implemented',
        } for pack in PACKS],
        'activity_packs': [{
            'unit_id': item.unit_id,
            'parent_unit_id': item.parent_unit_id,
            'task_id': item.task['id'],
            'content_version': item.version,
            'template_kinds': ['teach'] if item.flow == 'teach_cycle_v1' else sorted(item.templates),
            'flow': item.flow,
            'verification_task_id': item.verification_task['id'] if item.verification_task else None,
            'separate_development_approval_required': True,
            'student_text_understanding': False,
            'reasoning_quality_assessment': False,
        } for item in ACTIVITY_PACKS],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--output', type=Path, help='创建新快照，拒绝覆盖已有文件')
    mode.add_argument('--check', type=Path, help='只读检查已有快照是否与注册表一致')
    args = parser.parse_args()
    result = inventory()
    if args.check:
        if json.loads(args.check.read_text(encoding='utf-8')) != result:
            parser.exit(1, '内容快照与当前注册表不一致，请新增快照和覆盖台账更新记录。\n')
        print('Content inventory matches source registry.')
    elif args.output:
        # 历史文档保持不变：调用者应选择新的快照文件名。
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(f'Created inventory: {args.output.as_posix()}')
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
