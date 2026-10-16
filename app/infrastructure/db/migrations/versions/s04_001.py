"""LangGraph 计划确认流程、事务检查点和可续传事件。"""
from alembic import op
import sqlalchemy as sa

revision = "s04_001"
down_revision = "s02_003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("creation_key", sa.String(128), nullable=False),
        sa.Column("creation_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("draft", sa.JSON, nullable=True),
        sa.Column("gate_id", sa.String(36), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("event_seq", sa.Integer, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.ForeignKeyConstraint(["conversation_id", "workspace_id"], ["conversations.id", "conversations.workspace_id"]),
        sa.UniqueConstraint("workspace_id", "creation_key"),
        sa.CheckConstraint("status IN ('queued','awaiting_input','completed','cancelled','failed')"))
    op.create_index("ix_agent_runs_workspace_id", "agent_runs", ["workspace_id"])
    op.create_table("agent_checkpoints",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), primary_key=True),
        sa.Column("image", sa.Text, nullable=False))
    op.create_table("agent_events",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), primary_key=True),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE TRIGGER agent_events_immutable BEFORE UPDATE OR DELETE ON agent_events FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()")
    else:
        for action in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER agent_events_no_{action.lower()} BEFORE {action} ON agent_events BEGIN SELECT RAISE(ABORT, 'append-only record'); END")


def downgrade():
    op.drop_table("agent_events")
    op.drop_table("agent_checkpoints")
    op.drop_table("agent_runs")
