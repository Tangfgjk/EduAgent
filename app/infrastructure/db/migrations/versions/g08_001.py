"""Immutable offline Skill sources, candidates and mechanism evaluations."""
from alembic import op
import sqlalchemy as sa
revision='g08_001'
down_revision='g05_002'
branch_labels=None
depends_on=None


def upgrade():
    for table,parent in [('evolution_datasets','agent_runs'),('evolution_candidates','agent_runs'),('evolution_evaluations','evolution_candidates')]:
        extra=[]
        if table=='evolution_datasets':extra=[sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False)]
        if table=='evolution_candidates':extra=[sa.Column('dataset_id',sa.String(36),sa.ForeignKey('evolution_datasets.id'),nullable=False,unique=True),sa.Column('parent_strategy_id',sa.String(36),sa.ForeignKey('strategy_versions.id'),nullable=False)]
        op.create_table(table,sa.Column('id',sa.String(36),sa.ForeignKey(parent+'.id'),primary_key=True),*extra,
            sa.Column('body',sa.JSON,nullable=False),sa.Column('sha256',sa.String(64),nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
        if table=='evolution_datasets':op.create_index('ix_evolution_datasets_workspace_id',table,['workspace_id'])
        if op.get_bind().dialect.name=='sqlite':
            for action in ('UPDATE','DELETE'):op.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable evolution artifact'); END")
        else:
            op.execute(f"CREATE FUNCTION deny_{table}_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable evolution artifact'; END $$")
            op.execute(f'CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION deny_{table}_mutation()')


def downgrade():raise RuntimeError('Forward-only migration; restore a verified backup')
