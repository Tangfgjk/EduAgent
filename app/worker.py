"""教学检查点采用短事务；独立模型任务在事务外调用，并支持分进程运行。"""
import argparse
import logging
import time
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.infrastructure.db.database import Database
from app.infrastructure.db.models import Account, AgentCheckpoint, AgentRun, AnswerReview, LearningInput, OutboxJob, Workspace, LearningFact, now
from app.infrastructure.jobs.outbox import Outbox
from app.modules.errors import DomainError
from app.modules.learning.exposure import seen_before, present
from app.modules.learning.graph import VERSION, advance
from app.modules.learning.service import event
from app.modules.learning.content_service import require_content
from app.modules.learning.development_content import UNIT_ID
from app.modules.learning.content_catalog import get_pack
from app.modules.learning.tutor_graph import advance_tutor, version_for
from app.modules.memory.facts import publish_turn
from app.modules.planning.service import process_strategy
from app.modules.learning.activity_graph import advance_activity, version_for as activity_version_for
from app.modules.learning.activity_catalog import activity_for_run
from app.modules.learning.activity_facts import publish_activity
from app.modules.learning.activity_service import require_idle
from app.modules.learning.content_service import require_activity_content
from app.infrastructure.runtime.model_tasks import ModelTaskRunner, activate as activate_model_task

logger = logging.getLogger('wenjin.worker')


def run_loop(worker, *, stop=None, once=False, poll_seconds=0.5, lane='combined'):
    """Shared supervisor for CLI and embedded dev workers; never log exception payloads."""
    unavailable = False
    while stop is None or not stop.is_set():
        try:
            worked = worker.step()
            if unavailable:
                logger.warning('worker_cycle_recovered lane=%s', lane)
            unavailable = False
        except Exception:
            if not unavailable:
                logger.warning('worker_cycle_unavailable lane=%s code=cycle_unavailable', lane)
            unavailable, worked = True, False
        if once:
            return
        if not worked:
            if stop is None:
                time.sleep(poll_seconds)
            else:
                stop.wait(poll_seconds)


class Worker:
    def __init__(self, db, model_runner=None, enable_models=True):
        self.db, self.outbox = db, Outbox(db)
        self.model_runner = model_runner or ModelTaskRunner(db)
        self.enable_models = enable_models

    def reap(self):
        with self.db.transaction() as session:
            dead_inputs = select(LearningInput.conversation_id).join(OutboxJob, OutboxJob.input_id == LearningInput.id).where(OutboxJob.status == "dead")
            for run in session.scalars(select(AgentRun).where(AgentRun.status == "queued",
                    AgentRun.conversation_id.in_(dead_inputs)).with_for_update()):
                run.status, run.gate_id, run.error_code = "failed", None, "retries_exhausted"
                run.revision += 1
                event(session, run, "run.failed")

    def step(self):
        # Housekeeping must finish before taking a lease: an unavailable cycle
        # otherwise strands an untouched job for the full 60-second lease.
        self.reap()
        claimed = self.outbox.claim()
        if claimed is None:
            return self.model_runner.step() if self.enable_models else False
        started = time.monotonic()
        logger.info('teaching_job_claimed job_id=%s attempt=%s', claimed['job_id'], claimed['attempts'])
        try:
            with self.db.transaction() as session:
                entry = session.get(LearningInput, claimed["input_id"])
                # 与 HTTP 授权一致的锁顺序：账号 → 空间 → 运行 → 租约。
                account = session.get(Account, entry.account_id, with_for_update=True)
                workspace = session.get(Workspace, entry.workspace_id, with_for_update=True)
                run = session.scalar(select(AgentRun).where(AgentRun.id == entry.payload.get("run_id"),
                    AgentRun.workspace_id == entry.workspace_id, AgentRun.conversation_id == entry.conversation_id).with_for_update())
                job = session.get(OutboxJob, claimed["job_id"], with_for_update=True)
                self.check_lease(job, claimed)
                if run is None:
                    job.error_code = "unsupported_input"
                elif run.status == "queued" and run.kind == "course_build":
                    run.status = 'awaiting_input'
                    run.revision += 1
                    activate_model_task(session, run, entry)
                elif run.status == "queued" and run.kind == "skill_evolution":
                    from app.modules.skill_evolution.service import process as process_evolution
                    process_evolution(session,run,entry,account,workspace)
                elif run.status == "queued" and run.kind == "strategy":
                    process_strategy(session, run, entry, account, workspace)
                elif run.status == "queued":
                    try:
                        tutor_pack = get_pack(run.view["unit_id"] if run.view else entry.payload.get("unit_id", UNIT_ID), session) if run.kind == "tutor" else None
                        activity_pack = activity_for_run(run, entry.payload, session) if run.kind in {"roundtable", "teach"} else None
                        expected_version = {"plan": VERSION, "tutor": version_for(tutor_pack.unit_id, tutor_pack) if tutor_pack else None, "roundtable": activity_version_for(activity_pack.unit_id, activity_pack) if activity_pack else None, "teach": activity_version_for(activity_pack.unit_id, activity_pack) if activity_pack else None}.get(run.kind)
                        reason = "account_disabled" if not account.enabled else "workspace_archived" if workspace.status != "active" else "version_conflict" if run.version != expected_version else None
                    except DomainError as error:
                        reason = error.code
                    if run.kind in {"tutor", "roundtable", "teach"} and reason is None:
                        try:
                            if run.kind == "tutor":
                                require_content(session, account.source_kind, tutor_pack.unit_id, tutor_pack.version, workspace.id)
                                require_idle(session, workspace.id, ["roundtable", "teach"])
                            else:
                                require_activity_content(session, account.source_kind, activity_pack.unit_id, workspace.id)
                                require_idle(session, workspace.id, ["tutor"])
                        except DomainError as error:
                            reason = error.code
                    if reason:
                        run.status, run.error_code, run.gate_id = "failed", reason, None
                        event_kind = "run.failed"
                    else:
                        checkpoint = session.get(AgentCheckpoint, run.id)
                        execute = {"plan": advance, "tutor": advance_tutor, "roundtable": advance_activity, "teach": advance_activity}[run.kind]
                        invocation = dict(entry.payload)
                        if run.kind in {'teach','roundtable'} and run.view and run.view.get('phase') == 'verification':
                            task_id = run.view['verification_task']['task_id']
                            invocation['assessment_seen_before'] = seen_before(session,workspace.id,run.id,task_id)
                        state, image, paused = execute(run.id, account.learner_id, invocation,
                                                      checkpoint.image if checkpoint else None,
                                                      **({"item": tutor_pack or activity_pack} if run.kind != "plan" else {}))
                        if checkpoint:
                            checkpoint.image = image
                        else:
                            session.add(AgentCheckpoint(run_id=run.id, image=image))
                        if run.kind == "tutor":
                            previous_view = run.view
                            if state['view'].get('phase') in {'diagnostic','checkpoint'} and state['view'].get('task'):
                                task_id = state['view']['task']['task_id']
                                present(session,run,state['view']['task'],state['view']['content_version'])
                            run.view = state["view"]
                            publish_turn(session, run, entry, previous_view, state)
                            if state.get("review_task"):
                                task = next(item for item in tutor_pack.tasks if item["id"] == state["review_task"])
                                session.add(AnswerReview(input_id=entry.id, run_id=run.id, task_id=task["id"],
                                    content_version=tutor_pack.version, task_snapshot=task))
                        elif run.kind in {"roundtable", "teach"}:
                            if state['view'].get('phase') == 'verification':
                                task_id = state['view']['verification_task']['task_id']
                                present(session,run,state['view']['verification_task'],state['view']['activity_version'])
                            run.view = state["view"]
                            publish_activity(session, run, entry, state)
                        else:
                            run.draft = state["draft"]
                        run.status = "awaiting_input" if paused else "completed"
                        run.gate_id = state["gate_id"] if paused else None
                        event_kind = ("tutor." + entry.payload.get("action", "started") if paused else "tutor.completed") if run.kind == "tutor" else ("plan.proposed" if paused else "plan.confirmed")
                        if run.kind in {"roundtable", "teach"}:
                            event_kind = "activity.updated" if paused else "activity.completed"
                    run.revision += 1
                    activate_model_task(session, run, entry)
                    event(session, run, event_kind)
                # 取消、已终结运行不再执行图；输入历史仍保留。
                if run is not None:
                    from app.modules.skill_evolution.trials import monitor
                    monitor(session,run.workspace_id)
                    from app.modules.skill_evolution.automation import tick
                    tick(session,run.workspace_id)
                self.check_lease(job, claimed)
                if run is None:
                    job.status = "dead"
                else:
                    job.status, job.error_code = "completed", None
                job.lease_until, job.lease_token = None, None
        except Exception:
            logger.warning('teaching_job_failed job_id=%s attempt=%s code=runtime_error', claimed['job_id'], claimed['attempts'])
            # 事务已回滚，不输出学生原文、检查点、连接地址或原始异常。
            try:
                self.outbox.finish(claimed["job_id"], claimed["lease_token"], "runtime_error")
            except DomainError:
                pass
            self.reap()
        logger.info('teaching_job_cycle_finished job_id=%s elapsed_ms=%s', claimed['job_id'], int((time.monotonic() - started) * 1000))
        return True

    @staticmethod
    def check_lease(job, claimed):
        if job.status != "leased" or job.lease_token != claimed["lease_token"] or job.lease_until <= now():
            raise DomainError(409, "lease_lost", "任务租约已失效")


def main():
    parser = argparse.ArgumentParser(description="运行教学流程及已发布的开发模型任务；默认不开放模型")
    parser.add_argument("--env-file")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--lane", choices=["teaching", "models", "combined", "media"], default="combined",
                        help="试点分别启动 teaching/models 两个进程，避免模型等待占用教学 worker")
    args = parser.parse_args()
    settings = Settings.load(Path(args.env_file) if args.env_file else None)
    if not settings.database_url:
        parser.error("请配置 RSI_DATABASE_URL")
    db = Database(settings.database_url)
    try:
        db.assert_ready()
        if args.lane=='media':
            from app.modules.resources.media import MediaRunner
            worker=MediaRunner(db)
        else:worker = ModelTaskRunner(db) if args.lane == "models" else Worker(db, enable_models=args.lane == "combined")
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        run_loop(worker, once=args.once, lane=args.lane)
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("Worker startup unavailable; check database migration and configuration (details withheld).") from None
    finally:
        db.close()


if __name__ == "__main__":
    main()
