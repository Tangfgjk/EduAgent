"""Source-backed capability ledger; no marketing percentages or fabricated gates."""
from fastapi import APIRouter,Depends
from sqlalchemy import select,func
from app.infrastructure.db.models import ModelOperation,OpenMAICJob,MediaOperation,OutboxJob,GenerationPolicy,CourseBuild
from app.modules.knowledge.service import NODES
from app.modules.learning.content_catalog import PACKS
from app.modules.generation.rational_agent import FORMS,GOALS
from app.llm.catalog import catalog
from app.infrastructure.db.database import HEAD


def readiness_router(admin,settings):
    router=APIRouter(prefix='/api/v1/admin')
    @router.get('/readiness')
    def inspect(ctx=Depends(admin,scope='function')):
        def counts(model):return dict(ctx.session.execute(select(model.status,func.count()).group_by(model.status)).all())
        static=[{'unit_id':p.unit_id,'title':p.title,'grade':p.grade,'version':p.version,'task_count':len(p.tasks),
            'knowledge_points':list(p.knowledge_points),'verification_scope':p.boundary,'professional_review':'pending'} for p in PACKS]
        return {'schema_revision':HEAD,'scope':'初中数学模拟/成人开发；未开放真实学生模型调用',
            'coverage':{'static_units':static,'static_tasks':sum(len(p.tasks) for p in PACKS),
                'generation_families':[{'knowledge_id':kid,'family':n['family'],'title':n['title'],'grade':n['grade'],
                    'prerequisites':n['prerequisites'],'official_textbook_mapping':'not_verified'} for kid,n in NODES.items()],
                'rational_concepts':[{'concept':key,'title':GOALS[key],'forms':list(forms)} for key,forms in FORMS.items()],
                'course_states':counts(CourseBuild),'not_covered':['几何证明自动核验','作图与图像理解','通用方程/不等式解集','全学段合法教材逐页映射'],
                'notice':'统计来自实际注册表与持久课程，不是全课标完成比例。未覆盖主题必须澄清，不能偷换成已支持题型。'},
            'providers':catalog(),'queues':{'teaching':counts(OutboxJob),'models':counts(ModelOperation),'classrooms':counts(OpenMAICJob),'media':counts(MediaOperation)},
            'deployment':{'cookie_secure':settings.cookie_secure,'https_required_for_real_trial':True,'current_data_mode':settings.data_mode},
            'gates':{'teacher_rubric':'pending','real_student_effect':'not_validated','other_provider_live_tests':'pending_credentials_and_authorization',
                'media_live_test':'not_authorized','retention_policy':'not_enabled','privacy_export':'implemented','privacy_erasure':'explicit_offline_maintenance',
                'backup_restore':'maintenance_command_available_requires_verified_drill'},
            'notice':'教学效果、专业量规、正式隐私保存期和真实媒体预算分别验收；当前工程闭环不自动解除这些关口。'}
    return router
