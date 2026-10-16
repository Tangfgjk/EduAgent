"""已接收输入与审计只追加；应用和 worker 都不能覆盖历史。"""
from alembic import op

revision = "s02_002"
down_revision = "s02_001"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION reject_fact_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only record'; END; $$""")
        for table in ("learning_inputs", "audit_events"):
            op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()")
    else:
        for table in ("learning_inputs", "audit_events"):
            for action in ("UPDATE", "DELETE"):
                op.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'append-only record'); END")


def downgrade():
    for table in ("learning_inputs", "audit_events"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
        else:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER {table}_no_{action}")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION reject_fact_mutation()")
