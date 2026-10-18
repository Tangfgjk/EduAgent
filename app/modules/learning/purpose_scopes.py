"""Published transparency, not authorization for future model purposes."""
from app.modules.learning.contracts import Contract
from typing import Literal


class PurposeScope(Contract):
    purpose: str
    enabled: bool
    data_scope: str
    output_scope: str
    max_output_tokens: int
    account_daily_calls: int | None
    site_daily_calls: int | None
    budget_group: Literal['shared_development_calls','search_credits','resource_run_tokens'] = 'shared_development_calls'
    retry_policy: Literal['no_automatic_retry'] = 'no_automatic_retry'


def scopes(config, session=None):
    descriptions = (
        ('coach', '概率开发题、本轮想法及同题同空间历史（含本轮最多四轮），不含身份', '仅选择已审核追问方向'),
        ('teach', '概率活动、本次讲解及同活动最多最近两轮，不含独立验证题', '模型选择方向，安全模板展示追问；每活动最多三次'),
        ('roundtable', '概率活动、当前讲解与数值表达，不含其他项目或检测题', '同一模型三种视角，安全模板展示；每活动一次'),
        ('planning', '本项目最多六条脱敏记录、三条自述、体验、最多五份各八百字资料；全局仅计数', '仅开发策略两字段，不自动修改课程或 Skill'),
    )
    result = [PurposeScope(purpose=purpose, enabled=bool(config), data_scope=data, output_scope=output,
        max_output_tokens=config.max_output_tokens if config else 0,
        account_daily_calls=config.account_daily_calls if config else None,
        site_daily_calls=config.site_daily_calls if config else None).model_dump() for purpose, data, output in descriptions]
    for purpose in ('search', 'course_generation', 'resource_generation', 'skill_evolution'):
        result.append(PurposeScope(purpose=purpose, enabled=False, data_scope='未授权真实调用', output_scope='仅契约，尚未实现',
            max_output_tokens=0, account_daily_calls=None, site_daily_calls=None).model_dump())
    if session is not None:
        from sqlalchemy import select
        from app.infrastructure.db.models import GenerationPolicy, ResourceBudget
        from app.modules.knowledge.search import key
        policy=session.get(GenerationPolicy,1)
        by_id={r['purpose']:r for r in result}
        if policy and policy.interaction:
            for purpose in ('coach','teach','roundtable'):
                by_id[purpose]['data_scope']='已审核初中数学课程的当前题及脱敏本轮表达；我教 AI 可带本活动最近两轮；不含私有验证题或跨项目原文'
                by_id[purpose]['output_scope']='模型只选方向，安全模板展示；通用用途每活动最多三次，独立数值核验另行完成'
        by_id['course_generation'].update(enabled=bool(config and policy and policy.courses),
            data_scope='数学主题、已授权来源最多五份各八百字、当前项目脱敏计划和诊断计数',
            output_scope='七项参数化题候选及讲解；独立核验后待开发审核；每课程最多六次，明确内容修复最多一次',
            max_output_tokens=4096, account_daily_calls=config.account_daily_calls if config else None,
            site_daily_calls=config.site_daily_calls if config else None)
        by_id['search'].update(enabled=bool(policy and policy.search and key()),budget_group='search_credits',
            data_scope='仅标准初中数学关键词，固定三域白名单；无学生原文',output_scope='Tavily Basic 发现结果，不能直接作为已授权教材；每主题两次、全站每日二十次',
            max_output_tokens=0,account_daily_calls=None,site_daily_calls=20)
        resource_enabled=session.scalar(select(ResourceBudget.id).where(ResourceBudget.enabled.is_(True)).limit(1))
        by_id['resource_generation'].update(enabled=bool(config and config.vendor=='deepseek' and resource_enabled),budget_group='resource_run_tokens',
            data_scope='已审核数学主题及讲解，不含账号、学生诊断原文；本机独立 OpenMAIC',
            output_scope='首轮一门三场景文本；20 次请求；整次输入128000/输出32768 tokens；资源仍需审核',max_output_tokens=4096)
    return result
