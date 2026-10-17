"""Bounded model operations, published configurations and conservative daily budgets."""
from alembic import op
import sqlalchemy as sa

revision = "p0_001"
down_revision = "s09_001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    op.create_table("model_configurations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("vendor", sa.String(32), nullable=False), sa.Column("model", sa.String(160), nullable=False),
        sa.Column("max_output_tokens", sa.Integer, nullable=False),
        sa.Column("account_daily_calls", sa.Integer, nullable=False), sa.Column("site_daily_calls", sa.Integer, nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False))
    op.create_table("model_policy", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("active_id", sa.String(36), sa.ForeignKey("model_configurations.id")),
        sa.Column("revision", sa.Integer, nullable=False), sa.Column("stop_epoch", sa.Integer, nullable=False))
    op.create_table("model_daily_budgets", sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("scope", sa.String(64), primary_key=True), sa.Column("calls", sa.Integer, nullable=False))
    op.create_table("model_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("input_id", sa.String(36), sa.ForeignKey("learning_inputs.id"), nullable=False, unique=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("configuration_id", sa.String(36), sa.ForeignKey("model_configurations.id"), nullable=False),
        sa.Column("run_revision", sa.Integer, nullable=False), sa.Column("policy_epoch", sa.Integer, nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False), sa.Column("hint_level", sa.Integer, nullable=False),
        sa.Column("request", sa.JSON, nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.BigInteger), sa.Column("result", sa.JSON), sa.Column("error_code", sa.String(64)),
        sa.Column("usage", sa.JSON), sa.Column("returned_model", sa.String(160)), sa.Column("elapsed_ms", sa.Integer),
        sa.Column("billing_unknown", sa.Boolean, nullable=False), sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.Column("finished_at", sa.BigInteger),
        sa.CheckConstraint("status IN ('waiting','pending','running','succeeded','failed','cancelled','discarded','unknown')"))
    op.create_index("ix_model_operations_run_id", "model_operations", ["run_id"])
    op.create_index("ix_model_operations_ready", "model_operations", ["status", "created_at"])
    op.execute("INSERT INTO model_policy (id, active_id, revision, stop_epoch) VALUES (1, NULL, 0, 0)")
    if bind.dialect.name == "postgresql":
        op.execute("CREATE TRIGGER model_configurations_immutable BEFORE UPDATE OR DELETE ON model_configurations FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()")
    else:
        for action in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER model_configurations_no_{action.lower()} BEFORE {action} ON model_configurations BEGIN SELECT RAISE(ABORT, 'append-only record'); END")


def downgrade():
    for name in ("model_operations", "model_daily_budgets", "model_policy", "model_configurations"):
        op.drop_table(name)
