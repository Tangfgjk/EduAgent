"""独立版本与审核的互动包；原方程模板保持兼容。"""
from dataclasses import dataclass
from hashlib import sha256
import json

from app.modules.errors import DomainError
from app.modules.learning import activity_content as original
from app.modules.learning.content_catalog import get_pack


@dataclass(frozen=True)
class ActivityPack:
    unit_id: str
    title: str
    parent_unit_id: str
    task: dict
    templates: dict
    version: str
    flow: str = 'legacy'
    verification_task: dict | None = None

    @property
    def parent(self):
        return get_pack(self.parent_unit_id)

    def admin_tasks(self):
        return [{**self.task, 'activity_templates': self.templates, 'flow': self.flow,
                 'verification_task': self.verification_task,
                 'base_content_version': self.parent.version,
                 'boundary': '模板角色并不读取或评价学生文字；仅在最后核对数值，解释质量尚未评定。'}]


_parent = get_pack('math.g9.probability.dev1')
_task = next(task for task in _parent.tasks if task['id'] == 'P-PRB-02')
_templates = {
    'roundtable': [
        {'speaker': '思路提出者', 'text': '我建议先把每个球看作一个基本结果，再数蓝球和全部球的个数。', 'category': 'role_opinion'},
        {'speaker': '质疑者', 'text': '另一位学伴说：只有红、蓝两种颜色，所以摸到蓝球的概率是 1/2。两种颜色一定等可能吗？', 'category': 'role_opinion'},
        {'speaker': '整理者', 'text': '请比较按球数与按颜色种类计数的依据，确认等可能的基本结果，再给出概率并说明理由。', 'category': 'role_opinion'},
    ],
    'teach': [{'speaker': '虚拟学伴', 'text': '袋中只有红、蓝两种颜色，所以我认为摸到蓝球的概率是 1/2。请教我：应该按球还是按颜色计数？为什么？', 'category': 'preset_misconception'}],
}
_version = sha256(json.dumps({'task': _task, 'templates': _templates,
    'base_content_version': _parent.version, 'flow': 'student-first.v1'}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

_verification = next(task for task in _parent.tasks if task['id'] == 'C-PRB-02')

ACTIVITY_PACKS = (
    ActivityPack(original.UNIT_ID, original.TITLE, 'math.g7.equations.dev1', original.TASK, original.TEMPLATES, original.CONTENT_VERSION),
    ActivityPack('math.g9.probability.activities.dev1', '概率圆桌与我教 AI · 开发活动', _parent.unit_id, _task, _templates, _version),
    ActivityPack('math.g9.probability.teach.dev2', '概率我教 AI · 讲解与新题验证', _parent.unit_id, _task, _templates,
        sha256((_version + 'teach-cycle.v1:' + json.dumps(_verification, sort_keys=True, ensure_ascii=False)).encode()).hexdigest(),
        flow='teach_cycle_v1', verification_task=_verification),
)


def get_activity(unit_id=original.UNIT_ID):
    for item in ACTIVITY_PACKS:
        if item.unit_id == unit_id:
            return item
    raise DomainError(404, 'activity_not_found', '未找到此开发互动包')


def activity_for_version(version):
    for item in ACTIVITY_PACKS:
        if item.version == version:
            return item
    raise DomainError(409, 'version_conflict', '互动版本已变化，请重新开始；历史记录仍保留')


def activity_for_run(run, payload=None):
    return activity_for_version(run.view['activity_version']) if run.view else get_activity((payload or {}).get('unit_id', original.UNIT_ID))
