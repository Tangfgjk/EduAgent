"""Compile typed policy to one canonical, non-executable Markdown/DSL artifact."""
import hashlib
import json
from app.modules.planning.contracts import SoftPolicy
from app.modules.learning.content_repository import content_hash

COMPILER='skill-compiler.v1'
PROMPTS={'basis':'这一步用到了什么依据？下次准备怎么检查？','verification':'怎样把结果代回原条件检查？请写下你的核验与提醒。'}
CONSUMERS={'practice_order':'tutor.task_order','review_order':'memory.cards','support_mode':'tutor.support','question_focus':'generic_interaction.focus'}
LABELS={'practice_order':'练习顺序','review_order':'复习排序','support_mode':'生成课程支持方式','question_focus':'通用追问重点',
    'original':'原顺序','errors_first':'本项目同版本错题优先','due_first':'到期时间优先','due_errors_first':'到期数值错误来源优先',
    'step_first':'逐级支持','adaptive':'先自问，可恢复具体支持','basis':'依据与前提','representation':'表示条件','verification':'结果核验'}


def compile_policy(raw):
    policy=SoftPolicy.model_validate(raw).model_dump()
    digest=content_hash(policy)
    markdown='# 项目学习策略 · 开发候选\n\n- 每日建议：'+str(policy['daily_minutes'])+' 分钟（学生仍可自行决定）。\n- 反思引导：'+PROMPTS[policy['reflection_focus']]+'\n'
    if policy.get('execution'):
        markdown+='\n## 受限执行策略\n\n策略指纹：'+digest+'\n编译器：'+COMPILER+'\n'
        for key in CONSUMERS:markdown+='- '+LABELS[key]+'：'+LABELS[policy['execution'][key]]+'（'+CONSUMERS[key]+'）\n'
        markdown+='\n```json\n'+json.dumps(policy,ensure_ascii=False,sort_keys=True,indent=2)+'\n```\n'
    markdown+='\n教学硬规则、内容审核、考试隔离、验证与权限保持由平台执行。\n本文件是受限配置的可读导出，不作为任意代码或指令加载。\n'
    return {'compiler_version':COMPILER,'policy_hash':digest,'policy':policy,'markdown':markdown,
        'markdown_sha256':hashlib.sha256(markdown.encode()).hexdigest(),'consumers':dict(CONSUMERS) if policy.get('execution') else {'reflection_focus':'tutor.reflection'}}


def changes(before,after):
    rows=[]
    for key,value in after.items():
        if key=='execution':
            for name in CONSUMERS:
                old=(before.get('execution') or {}).get(name)
                if old!=value[name]:rows.append({'field':name,'label':LABELS[name],'before':LABELS.get(old,old) if old else '默认/旧版未指定','after':LABELS[value[name]],'consumer':CONSUMERS[name]})
        elif before.get(key)!=value:rows.append({'field':key,'label':key,'before':before.get(key),'after':value,'consumer':'tutor.reflection' if key=='reflection_focus' else 'plan.suggested_minutes'})
    return rows
