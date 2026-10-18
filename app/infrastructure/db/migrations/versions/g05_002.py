"""Record assessment presentation even when a learner cancels without answering."""
from alembic import op
import sqlalchemy as sa

revision='g05_002'
down_revision='g05_001'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('assessment_exposures',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False),
        sa.Column('run_id',sa.String(36),sa.ForeignKey('agent_runs.id'),nullable=False),
        sa.Column('task_id',sa.String(100),nullable=False),
        sa.Column('content_version',sa.String(64),nullable=False),
        sa.Column('created_at',sa.BigInteger,nullable=False),
        sa.UniqueConstraint('run_id','task_id','content_version'))
    op.create_index('ix_assessment_exposures_workspace_id','assessment_exposures',['workspace_id'])
    if op.get_bind().dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER immutable_assessment_exposures_{action.lower()} BEFORE {action} ON assessment_exposures BEGIN SELECT RAISE(ABORT, 'immutable assessment exposure'); END")
    else:
        op.execute("CREATE FUNCTION deny_assessment_exposure_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable assessment exposure'; END $$")
        op.execute('CREATE TRIGGER immutable_assessment_exposures BEFORE UPDATE OR DELETE ON assessment_exposures FOR EACH ROW EXECUTE FUNCTION deny_assessment_exposure_mutation()')


def downgrade():
    raise RuntimeError('Forward-only development migration; restore a verified backup to roll back')
