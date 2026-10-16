from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.modules.evidence.contracts import Category, EvidenceDays, EvidenceOut, Source
from app.modules.evidence.service import Evidence


def evidence_router(student):
    router, evidence = APIRouter(prefix="/api/v1/student"), Evidence()
    auth = Depends(student, scope="function")

    @router.get("/evidence", response_model=EvidenceOut)
    def global_report(ctx=auth, days: EvidenceDays = EvidenceDays.month, source: Source = "all",
                      record_kind: Category = "all", offset: int = Query(0, ge=0)):
        return evidence.report(ctx.session, ctx.actor, days=days, source=source, record_kind=record_kind, offset=offset)

    @router.get("/workspaces/{wid}/evidence", response_model=EvidenceOut)
    def local_report(wid: UUID, ctx=auth, days: EvidenceDays = EvidenceDays.month, source: Source = "all",
                     record_kind: Category = "all", offset: int = Query(0, ge=0)):
        return evidence.report(ctx.session, ctx.actor, str(wid), days, source, record_kind, offset)

    return router
