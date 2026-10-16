from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response

from app.modules.learning.contracts import RunRevision
from app.modules.planning.contracts import ActiveOut, MaterialIn, MaterialOut, MaterialsOut, ReleaseIn, RollbackIn, StartStrategy, StrategiesOut, StrategyDecision, StrategyOut
from app.modules.planning.service import Materials, Planning, public_strategy


def planning_router(student, admin):
    router, planning, materials = APIRouter(prefix="/api/v1"), Planning(), Materials()
    student_auth, admin_auth = Depends(student, scope="function"), Depends(admin, scope="function")
    key_header = Header(alias="Idempotency-Key", min_length=1, max_length=128)
    prefix = "/student/workspaces/{wid}/strategies"

    @router.get("/admin/planning-materials", response_model=MaterialsOut)
    def admin_materials(ctx=admin_auth, offset: int = Query(0, ge=0)):
        return materials.list(ctx.session, True, offset)

    @router.get("/student/planning-materials", response_model=MaterialsOut)
    def available_materials(ctx=student_auth, offset: int = Query(0, ge=0)):
        return materials.list(ctx.session, False, offset)

    @router.post("/admin/planning-materials", response_model=MaterialOut, status_code=201)
    def add_material(body: MaterialIn, ctx=admin_auth):
        return materials.create(ctx.session, ctx.actor, body.model_dump())

    @router.post("/admin/planning-materials/{mid}/release", response_model=MaterialOut)
    def release(mid: UUID, body: ReleaseIn, ctx=admin_auth):
        return materials.release(ctx.session, ctx.actor, str(mid), body.model_dump())

    @router.get(prefix, response_model=StrategiesOut)
    def runs(wid: UUID, ctx=student_auth, offset: int = Query(0, ge=0)):
        return planning.list(ctx.session, ctx.actor, str(wid), offset)

    @router.post(prefix, response_model=StrategyOut, status_code=202)
    def start(wid: UUID, body: StartStrategy, ctx=student_auth, key: str = key_header):
        return planning.start(ctx.session, ctx.actor, str(wid), body.model_dump(mode="json"), key)

    @router.get(prefix + "/{rid}", response_model=StrategyOut)
    def run(wid: UUID, rid: UUID, ctx=student_auth):
        return public_strategy(ctx.session, planning.owned(ctx.session, ctx.actor, str(wid), str(rid)))

    @router.post(prefix + "/{rid}/decision", response_model=StrategyOut, status_code=202)
    def student_decide(wid: UUID, rid: UUID, body: StrategyDecision, ctx=student_auth, key: str = key_header):
        return planning.decide(ctx.session, ctx.actor, planning.owned(ctx.session, ctx.actor, str(wid), str(rid)), body.model_dump(mode="json"), key)

    @router.post(prefix + "/{rid}/cancel", response_model=StrategyOut)
    def cancel(wid: UUID, rid: UUID, body: RunRevision, ctx=student_auth):
        return planning.cancel(ctx.session, ctx.actor, str(wid), str(rid), body.expected_revision)

    @router.get(prefix + "/{rid}/skill", response_class=Response)
    def export_skill(wid: UUID, rid: UUID, ctx=student_auth):
        result = public_strategy(ctx.session, planning.owned(ctx.session, ctx.actor, str(wid), str(rid)))
        return Response(result["skill_markdown"] or "# 候选尚未生成\n", media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="Skill.md"'})

    @router.get("/student/workspaces/{wid}/active-strategy", response_model=ActiveOut)
    def active(wid: UUID, ctx=student_auth):
        return planning.active(ctx.session, ctx.actor, str(wid))

    @router.post("/student/workspaces/{wid}/active-strategy/rollback", response_model=ActiveOut)
    def rollback(wid: UUID, body: RollbackIn, ctx=student_auth, key: str = key_header):
        return planning.rollback(ctx.session, ctx.actor, str(wid), body.model_dump(mode="json"), key)

    @router.get("/admin/strategies", response_model=StrategiesOut)
    def queue(ctx=admin_auth, offset: int = Query(0, ge=0)):
        return planning.admin_list(ctx.session, offset)

    @router.get("/admin/strategies/{rid}", response_model=StrategyOut)
    def inspect(rid: UUID, ctx=admin_auth):
        return public_strategy(ctx.session, planning.admin_run(ctx.session, str(rid)))

    @router.post("/admin/strategies/{rid}/decision", response_model=StrategyOut, status_code=202)
    def admin_decide(rid: UUID, body: StrategyDecision, ctx=admin_auth, key: str = key_header):
        return planning.decide(ctx.session, ctx.actor, planning.admin_run(ctx.session, str(rid)), body.model_dump(mode="json"), key, admin=True)

    return router
