"""不可变学习事实、复习安排与来源清晰的纠正记录。"""
from alembic import op
import sqlalchemy as sa

revision = "s06_001"
down_revision = "s05_001"
branch_labels = None
depends_on = None

IMMUTABLE = ("learning_facts", "fact_judgments", "recall_attempts", "memory_notes")


def upgrade():
    op.create_table("learning_facts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("input_id", sa.String(36), sa.ForeignKey("learning_inputs.id"), nullable=False, unique=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("content_version", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False))
    op.create_index("ix_learning_facts_workspace_id", "learning_facts", ["workspace_id"])
    op.create_table("fact_judgments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fact_id", sa.String(36), sa.ForeignKey("learning_facts.id"), nullable=False),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("answer_reviews.id"), nullable=False, unique=True),
        sa.Column("conclusion", sa.String(24), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False))
    op.create_index("ix_fact_judgments_fact_id", "fact_judgments", ["fact_id"])
    op.create_table("review_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("content_version", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_fact_ids", sa.JSON, nullable=False),
        sa.Column("reasons", sa.JSON, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("schedule_step", sa.Integer, nullable=False),
        sa.Column("next_due_at", sa.BigInteger, nullable=False),
        sa.Column("last_reviewed_at", sa.BigInteger, nullable=True),
        sa.UniqueConstraint("workspace_id", "task_id", "content_version", "source_kind"))
    op.create_index("ix_review_cards_workspace_id", "review_cards", ["workspace_id"])
    op.create_table("recall_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("card_id", sa.String(36), sa.ForeignKey("review_cards.id"), nullable=False),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("input_id", sa.String(36), sa.ForeignKey("learning_inputs.id"), nullable=False, unique=True),
        sa.Column("fact_id", sa.String(36), sa.ForeignKey("learning_facts.id"), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("counted_for_schedule", sa.Boolean, nullable=False),
        sa.Column("due_before", sa.BigInteger, nullable=False),
        sa.Column("due_after", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("workspace_id", "idempotency_key"))
    op.create_index("ix_recall_attempts_card_id", "recall_attempts", ["card_id"])
    op.create_table("memory_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("fact_id", sa.String(36), sa.ForeignKey("learning_facts.id"), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("text", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("workspace_id", "idempotency_key"))
    op.create_index("ix_memory_notes_workspace_id", "memory_notes", ["workspace_id"])
    if op.get_bind().dialect.name == "postgresql":
        for table in IMMUTABLE:
            op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()")
    else:
        for table in IMMUTABLE:
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only record'); END")


def downgrade():
    for table in ("memory_notes", "recall_attempts", "review_cards", "fact_judgments", "learning_facts"):
        op.drop_table(table)
