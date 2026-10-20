from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.modules.evidence.support_filter import Assistance
from app.modules.evidence.contracts import Category, EvidenceDays, EvidenceOut, Source
from app.modules.evidence.service import Evidence
from app.modules.evidence.insight_contracts import LearningInsights
from app.modules.evidence.insights import report as insights


def evidence_router(student):
    router, evidence = APIRouter(prefix="/api/v1/student"), Evidence()
    auth = Depends(student, scope="function")

    @router.get('/learning-insights',response_model=LearningInsights)
    def global_insights(ctx=auth,days:EvidenceDays=EvidenceDays.month,source:Source='all',include_archived:bool=True,assistance:Assistance='all'):
        return insights(ctx.session,ctx.actor,days=days,source=source,include_archived=include_archived,assistance=assistance)

    @router.get('/workspaces/{wid}/learning-insights',response_model=LearningInsights)
    def local_insights(wid:UUID,ctx=auth,days:EvidenceDays=EvidenceDays.month,source:Source='all',assistance:Assistance='all'):
        return insights(ctx.session,ctx.actor,str(wid),days,source,assistance=assistance)

    @router.get("/evidence", response_model=EvidenceOut)
    def global_report(ctx=auth, days: EvidenceDays = EvidenceDays.month, source: Source = "all",
                      record_kind: Category = "all", offset: int = Query(0, ge=0),include_archived:bool=True,assistance:Assistance='all'):
        return evidence.report(ctx.session, ctx.actor, days=days, source=source, record_kind=record_kind, offset=offset,include_archived=include_archived,assistance=assistance)

    @router.get("/workspaces/{wid}/evidence", response_model=EvidenceOut)
    def local_report(wid: UUID, ctx=auth, days: EvidenceDays = EvidenceDays.month, source: Source = "all",
                     record_kind: Category = "all", offset: int = Query(0, ge=0),include_archived:bool=True,assistance:Assistance='all'):
        return evidence.report(ctx.session, ctx.actor, str(wid), days, source, record_kind, offset,assistance=assistance)

    return router
