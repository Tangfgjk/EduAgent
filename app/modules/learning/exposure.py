"""Presentation is exposure; an unanswered, cancelled task is still seen."""
from sqlalchemy import select
from app.infrastructure.db.models import AssessmentExposure,LearningFact


def seen_before(session,wid,rid,task_id):
    for model in (AssessmentExposure,LearningFact):
        if session.scalar(select(model.id).where(model.workspace_id==wid,model.run_id!=rid,model.task_id==task_id).limit(1)):
            return True
    return False


def present(session,run,task,version):
    exposed=seen_before(session,run.workspace_id,run.id,task['task_id'])
    prior=session.scalar(select(AssessmentExposure.id).where(AssessmentExposure.run_id==run.id,
        AssessmentExposure.task_id==task['task_id'],AssessmentExposure.content_version==version))
    if not prior:
        session.add(AssessmentExposure(workspace_id=run.workspace_id,run_id=run.id,task_id=task['task_id'],content_version=version))
    task['exposure_status']='seen_before' if exposed else 'fresh'
