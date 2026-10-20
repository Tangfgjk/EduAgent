"""Versioned development reference: teaching principles, not textbook claims."""
from app.modules.learning.service import request_hash
from app.modules.errors import DomainError


def reference():
    body = {'source_id': 'builtin.teaching-principles.v1', 'version': '1',
        'title': '初中数学自主学习开发基准', 'kind': 'development_reference',
        'authorization': 'project_authored', 'professional_review': False,
        'body': '先明确学习目标，依据当前任务中的真实表现调整支持；学生先尝试，再按需获得提示。'
                '数值结果、学生自述和解释质量分别记录；独立检测不提供解题提示。'
                '每日节奏由学生选择并可调整；练习与反思不代表掌握度或教学效果。'
                '优先复习已有错误时保留诊断和检测；支持可以渐撤，也可以恢复。'}
    return {**body, 'content_hash': request_hash(body)}


def check(bundle):
    frozen = bundle.get('default_reference')
    if frozen is not None and frozen != reference():
        raise DomainError(409, 'default_reference_changed', '默认教学基准版本已变化，请重新生成策略')
