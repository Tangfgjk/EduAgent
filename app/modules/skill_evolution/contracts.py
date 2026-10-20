from typing import Literal
from pydantic import Field,field_validator
from app.modules.learning.contracts import Contract

class EvolutionStart(Contract):
    mode: Literal['offline', 'semantic']='offline'
    allow_personal_trial: bool = Field(default=False, strict=True)

class EvolutionDecision(Contract):
    expected_revision: int=Field(ge=1,strict=True)
    action: Literal['approve','reject','withdraw']
    report_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    note: str=Field(min_length=10,max_length=1000)
    @field_validator('note')
    @classmethod
    def clean(cls,value):
        if len(value.strip())<10:raise ValueError('请补充至少十字的复核依据')
        return value.strip()

class EvolutionProposal(Contract):
    expected_revision: int=Field(ge=1,strict=True)
    report_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    note: str=Field(min_length=10,max_length=1000)
    @field_validator('note')
    @classmethod
    def clean(cls,value):return EvolutionDecision.clean(value)

class EvolutionOut(Contract):
    generation: dict | None = None
    trial: dict | None = None
    run_id: str
    workspace_id: str
    workspace_title: str
    workspace_status: str
    status: str
    phase: str
    revision: int
    error_code: str|None
    reason: str|None=None
    dataset: dict|None=None
    candidate: dict|None=None
    evaluation: dict|None=None
    report_sha256: str|None=None
    reviews: list[dict]
    strategy_run_id: str|None=None
    created_at: int
    automatic_publish: Literal[False]=False

class EvolutionsOut(Contract):
    items: list[EvolutionOut]
