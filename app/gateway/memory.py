from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from app.modules.memory.contracts import CardOut, CardsOut, FactOut, MemoryOut, NoteIn, NoteOut, RecallIn, RecallResult, UnknownsOut
from app.modules.memory.projections import rebuild
from app.modules.memory.service import Memory


def memory_router(student):
    router, memory = APIRouter(prefix="/api/v1/student"), Memory()
    auth = Depends(student, scope="function")
    key_header = Header(alias="Idempotency-Key", min_length=1, max_length=128)
    prefix = "/workspaces/{wid}"

    @router.get(prefix + "/review-cards", response_model=CardsOut)
    def cards(wid: UUID, ctx=auth, status: Literal["all", "due", "scheduled"] = "all", offset: int = Query(0, ge=0)):
        return memory.cards(ctx.session, ctx.actor, str(wid), status, offset)

    @router.get(prefix + "/review-cards/{cid}", response_model=CardOut)
    def card(wid: UUID, cid: UUID, ctx=auth):
        return memory.card_out(ctx.session, memory.card(ctx.session, ctx.actor, str(wid), str(cid)))

    @router.post(prefix + "/review-cards/{cid}/attempts", response_model=RecallResult)
    def recall(wid: UUID, cid: UUID, body: RecallIn, ctx=auth, key: str = key_header):
        return memory.recall(ctx.session, ctx.actor, str(wid), str(cid), body.model_dump(), key)

    @router.get(prefix + "/unverifiable-answers", response_model=UnknownsOut)
    def unknowns(wid: UUID, ctx=auth, offset: int = Query(0, ge=0)):
        return memory.unknowns(ctx.session, ctx.actor, str(wid), offset)

    @router.get(prefix + "/facts/{fid}", response_model=FactOut)
    def fact(wid: UUID, fid: UUID, ctx=auth):
        return memory.fact(ctx.session, ctx.actor, str(wid), str(fid))

    @router.post(prefix + "/memory-notes", response_model=NoteOut, status_code=201)
    def note(wid: UUID, body: NoteIn, ctx=auth, key: str = key_header):
        return memory.add_note(ctx.session, ctx.actor, str(wid), body.model_dump(mode="json"), key)

    @router.get(prefix + "/memory", response_model=MemoryOut)
    def local(wid: UUID, ctx=auth):
        return memory.local(ctx.session, ctx.actor, str(wid))

    @router.post(prefix + "/memory/rebuild", response_model=MemoryOut)
    def regenerate(wid: UUID, ctx=auth):
        memory.spaces.owned(ctx.session, ctx.actor, str(wid), active=True)
        rebuild(ctx.session, str(wid))
        return memory.local(ctx.session, ctx.actor, str(wid))

    @router.get("/memory", response_model=MemoryOut)
    def global_memory(ctx=auth, offset: int = Query(0, ge=0)):
        return memory.global_memory(ctx.session, ctx.actor, offset)

    return router
