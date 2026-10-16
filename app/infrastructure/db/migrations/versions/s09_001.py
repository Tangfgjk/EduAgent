"""规划资料、不可变候选/回执/激活历史与当前策略指针。"""
from alembic import op
import sqlalchemy as sa

revision = "s09_001"
down_revision = "s06_001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("planning_materials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False), sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("edition", sa.String(80), nullable=False), sa.Column("source_ref", sa.String(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False), sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False))
    op.create_table("material_releases",
        sa.Column("material_id", sa.String(36), sa.ForeignKey("planning_materials.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean, nullable=False), sa.Column("revision", sa.Integer, nullable=False))
    op.create_table("strategy_versions",
        sa.Column("id", sa.String(36), sa.ForeignKey("agent_runs.id"), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("bundle", sa.JSON, nullable=False), sa.Column("policy", sa.JSON, nullable=False),
        sa.Column("checks", sa.JSON, nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("hard_rules_hash", sa.String(64), nullable=False), sa.Column("base_revision", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.BigInteger, nullable=False), sa.Column("created_at", sa.BigInteger, nullable=False))
    op.create_index("ix_strategy_versions_workspace_id", "strategy_versions", ["workspace_id"])
    op.create_table("strategy_receipts",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("input_id", sa.String(36), sa.ForeignKey("learning_inputs.id"), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False), sa.Column("action", sa.String(24), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False), sa.Column("created_at", sa.BigInteger, nullable=False))
    op.create_index("ix_strategy_receipts_run_id", "strategy_receipts", ["run_id"])
    op.create_table("active_strategies",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("revision", sa.Integer, nullable=False))
    op.create_table("strategy_activations",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("from_version", sa.String(36), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("to_version", sa.String(36), sa.ForeignKey("strategy_versions.id"), nullable=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False), sa.UniqueConstraint("workspace_id", "idempotency_key"))
    op.create_index("ix_strategy_activations_workspace_id", "strategy_activations", ["workspace_id"])
    for table in ("planning_materials", "strategy_versions", "strategy_receipts", "strategy_activations"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()")
        else:
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only record'); END")


def downgrade():
    for table in ("strategy_activations", "active_strategies", "strategy_receipts", "strategy_versions", "material_releases", "planning_materials"):
        op.drop_table(table)
