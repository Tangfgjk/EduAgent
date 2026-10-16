from __future__ import annotations

from sqlalchemy import and_, or_, select, update

from app.infrastructure.db.database import Database
from app.infrastructure.db.models import OutboxJob, new_id, now
from app.modules.errors import DomainError


class Outbox:
    """租约与 fencing token 防止旧 worker 提交；任务处理器在 S04 接入。"""
    def __init__(self, db: Database):
        self.db = db

    def claim(self, lease_seconds: int = 60) -> dict | None:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("租约必须为 1–3600 秒")
        with self.db.transaction() as session:
            timestamp = now()
            session.execute(update(OutboxJob).where(OutboxJob.status == "leased",
                OutboxJob.lease_until <= timestamp, OutboxJob.attempts >= 5).values(
                    status="dead", error_code="lease_expired", lease_token=None, lease_until=None))
            job = session.scalar(select(OutboxJob).where(or_(
                and_(OutboxJob.status == "pending", OutboxJob.available_at <= timestamp),
                and_(OutboxJob.status == "leased", OutboxJob.lease_until <= timestamp)
            ), OutboxJob.attempts < 5).order_by(OutboxJob.available_at, OutboxJob.id).with_for_update(skip_locked=True).limit(1))
            if job is None:
                return None
            job.status, job.lease_until, job.lease_token = "leased", timestamp + lease_seconds, new_id()
            job.attempts += 1
            return {"job_id": job.id, "input_id": job.input_id, "lease_token": job.lease_token, "attempts": job.attempts}

    def finish(self, job_id: str, token: str, error_code: str | None = None):
        with self.db.transaction() as session:
            job = session.get(OutboxJob, job_id, with_for_update=True)
            if job is None or job.status != "leased" or job.lease_token != token or job.lease_until <= now():
                raise DomainError(409, "lease_lost", "任务租约已失效")
            if error_code:
                if len(error_code) > 64:
                    raise ValueError("仅记录简短错误码，不写入原始异常")
                job.status = "dead" if job.attempts >= 5 else "pending"
                job.available_at, job.error_code = now() + min(60, 2 ** job.attempts), error_code
            else:
                job.status = "completed"
                job.error_code = None
            job.lease_until = None
            job.lease_token = None
