"""开发内容启用、TutorGraph 视图与不可验证解答复核。"""
from alembic import op
import sqlalchemy as sa

revision = "s05_001"
down_revision = "s04_001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("kind", sa.String(16), nullable=False, server_default="plan"))
    op.add_column("agent_runs", sa.Column("view", sa.JSON, nullable=True))
    op.create_table("content_approvals",
        sa.Column("unit_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("reviewed_at", sa.BigInteger, nullable=False))
    op.create_table("answer_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("input_id", sa.String(36), sa.ForeignKey("learning_inputs.id"), nullable=False, unique=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("content_version", sa.String(64), nullable=False),
        sa.Column("task_snapshot", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("conclusion", sa.String(24), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("created_at", sa.BigInteger, nullable=False))
    op.create_index("ix_answer_reviews_run_id", "answer_reviews", ["run_id"])


def downgrade():
    op.drop_table("answer_reviews")
    op.drop_table("content_approvals")
    op.drop_column("agent_runs", "view")
    op.drop_column("agent_runs", "kind")
