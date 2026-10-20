"""Private automation, immutable evaluation observations and classroom resume."""
from alembic import op
import sqlalchemy as sa

revision='r14_001'
down_revision='g08_001'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('generation_policy',sa.Column('private_auto_release',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_table('skill_trials',sa.Column('id',sa.String(36),sa.ForeignKey('evolution_candidates.id'),primary_key=True),
        sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False),
        sa.Column('baseline_id',sa.String(36),sa.ForeignKey('strategy_versions.id'),nullable=False),
        sa.Column('candidate_id',sa.String(36),sa.ForeignKey('strategy_versions.id'),nullable=False),
        sa.Column('status',sa.String(32),nullable=False),sa.Column('body',sa.JSON,nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_index('ix_skill_trials_workspace_id','skill_trials',['workspace_id'])
    op.create_table('evaluation_studies',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('owner_id',sa.String(36),sa.ForeignKey('accounts.id'),nullable=False),sa.Column('title',sa.String(160),nullable=False),
        sa.Column('protocol',sa.JSON,nullable=False),sa.Column('status',sa.String(32),nullable=False),sa.Column('revision',sa.Integer,nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_table('evaluation_observations',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('study_id',sa.String(36),sa.ForeignKey('evaluation_studies.id'),nullable=False),sa.Column('creation_key',sa.String(128),nullable=False),
        sa.Column('body',sa.JSON,nullable=False),sa.Column('sha256',sa.String(64),nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False),sa.UniqueConstraint('study_id','creation_key'))
    op.create_index('ix_evaluation_observations_study_id','evaluation_observations',['study_id'])
    op.create_table('resource_progress',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('account_id',sa.String(36),sa.ForeignKey('accounts.id'),nullable=False),sa.Column('resource_id',sa.String(36),sa.ForeignKey('resource_artifacts.id'),nullable=False),
        sa.Column('body',sa.JSON,nullable=False),sa.Column('revision',sa.Integer,nullable=False),sa.Column('updated_at',sa.BigInteger,nullable=False),sa.UniqueConstraint('account_id','resource_id'))
    op.create_table('learning_automation',sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),primary_key=True),
        sa.Column('enabled',sa.Boolean,nullable=False),sa.Column('revision',sa.Integer,nullable=False),sa.Column('body',sa.JSON,nullable=False))
    if op.get_bind().dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER immutable_evaluation_observations_{action.lower()} BEFORE {action} ON evaluation_observations BEGIN SELECT RAISE(ABORT, 'immutable evaluation observation'); END")
    else:
        op.execute("CREATE FUNCTION deny_evaluation_observation_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable evaluation observation'; END $$")
        op.execute('CREATE TRIGGER immutable_evaluation_observations BEFORE UPDATE OR DELETE ON evaluation_observations FOR EACH ROW EXECUTE FUNCTION deny_evaluation_observation_mutation()')


def downgrade():raise RuntimeError('Forward-only migration; restore a verified backup')
