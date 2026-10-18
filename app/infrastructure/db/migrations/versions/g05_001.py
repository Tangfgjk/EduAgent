"""Topic preparation, immutable sources, private course builds and resources."""
from alembic import op
import sqlalchemy as sa

revision='g05_001'
down_revision='g01_001'
branch_labels=None
depends_on=None


def upgrade():
    if op.get_bind().dialect.name=='postgresql':
        op.alter_column('content_approvals','unit_id',existing_type=sa.String(64),type_=sa.String(100),existing_nullable=False)
    if op.get_bind().dialect.name=='sqlite':
        op.execute('ALTER TABLE generated_content_versions ADD COLUMN workspace_id VARCHAR(36) REFERENCES workspaces(id)')
    else:
        op.add_column('generated_content_versions',sa.Column('workspace_id',sa.String(36),nullable=True))
        op.create_foreign_key('fk_generated_content_workspace','generated_content_versions','workspaces',['workspace_id'],['id'])
    op.create_index('ix_generated_content_versions_workspace_id','generated_content_versions',['workspace_id'])
    op.create_table('knowledge_documents',sa.Column('id',sa.String(36),primary_key=True),sa.Column('version',sa.String(64),nullable=False),
        sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id')),sa.Column('body',sa.JSON,nullable=False),
        sa.Column('creator_id',sa.String(36),sa.ForeignKey('accounts.id'),nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_table('knowledge_source_states',sa.Column('source_id',sa.String(100),primary_key=True),sa.Column('enabled',sa.Boolean,nullable=False),sa.Column('revision',sa.Integer,nullable=False))
    op.create_table('generation_policy',sa.Column('id',sa.Integer,primary_key=True),sa.Column('revision',sa.Integer,nullable=False),
        sa.Column('courses',sa.Boolean,nullable=False),sa.Column('interaction',sa.Boolean,nullable=False),sa.Column('search',sa.Boolean,nullable=False))
    op.execute(sa.text('INSERT INTO generation_policy (id,revision,courses,interaction,search) VALUES (1,0,false,false,false)'))
    op.create_table('course_builds',sa.Column('id',sa.String(36),primary_key=True),sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False),
        sa.Column('run_id',sa.String(36),sa.ForeignKey('agent_runs.id')),sa.Column('creation_key',sa.String(128),nullable=False),
        sa.Column('topic',sa.String(600),nullable=False),sa.Column('intent',sa.JSON,nullable=False),sa.Column('sources',sa.JSON,nullable=False),
        sa.Column('diagnostics',sa.JSON,nullable=False),sa.Column('attempts',sa.JSON,nullable=False),sa.Column('plan',sa.JSON),sa.Column('candidate',sa.JSON),
        sa.Column('validation',sa.JSON),sa.Column('status',sa.String(40),nullable=False),sa.Column('revision',sa.Integer,nullable=False),sa.Column('unit_id',sa.String(100)),
        sa.Column('repair_count',sa.Integer,nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False),sa.UniqueConstraint('workspace_id','creation_key'))
    op.create_index('ix_course_builds_workspace_id','course_builds',['workspace_id'])
    op.create_table('resource_artifacts',sa.Column('id',sa.String(36),primary_key=True),sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False),
        sa.Column('build_id',sa.String(36),sa.ForeignKey('course_builds.id'),nullable=False),sa.Column('body',sa.JSON,nullable=False),sa.Column('sha256',sa.String(64),nullable=False),
        sa.Column('status',sa.String(24),nullable=False),sa.Column('revision',sa.Integer,nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_index('ix_resource_artifacts_workspace_id','resource_artifacts',['workspace_id'])
    op.create_table('search_operations',sa.Column('id',sa.String(36),primary_key=True),sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False),
        sa.Column('build_id',sa.String(36),sa.ForeignKey('course_builds.id'),nullable=False),sa.Column('query',sa.String(160),nullable=False),
        sa.Column('status',sa.String(24),nullable=False),sa.Column('result',sa.JSON),sa.Column('credits',sa.Integer),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_index('ix_search_operations_workspace_id','search_operations',['workspace_id'])
    op.create_table('production_reviews',sa.Column('id',sa.String(36),primary_key=True),sa.Column('target_kind',sa.String(24),nullable=False),
        sa.Column('target_id',sa.String(36),nullable=False),sa.Column('revision',sa.Integer,nullable=False),sa.Column('decision',sa.String(24),nullable=False),
        sa.Column('note',sa.String(2000),nullable=False),sa.Column('reviewer_id',sa.String(36),sa.ForeignKey('accounts.id'),nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_index('ix_production_reviews_target_id','production_reviews',['target_id'])
    op.create_table('resource_budgets',sa.Column('id',sa.String(36),primary_key=True),sa.Column('secret_hash',sa.String(64),nullable=False,unique=True),
        sa.Column('enabled',sa.Boolean,nullable=False),sa.Column('calls',sa.Integer,nullable=False),sa.Column('input_reserved',sa.Integer,nullable=False),
        sa.Column('output_reserved',sa.Integer,nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_table('resource_model_calls',sa.Column('id',sa.String(36),primary_key=True),sa.Column('budget_id',sa.String(36),sa.ForeignKey('resource_budgets.id'),nullable=False),
        sa.Column('request_hash',sa.String(64),nullable=False),sa.Column('status',sa.String(24),nullable=False),sa.Column('input_reserved',sa.Integer,nullable=False),
        sa.Column('output_reserved',sa.Integer,nullable=False),sa.Column('usage',sa.JSON),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_index('ix_resource_model_calls_budget_id','resource_model_calls',['budget_id'])
    op.create_table('openmaic_jobs',sa.Column('id',sa.String(36),primary_key=True),sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False),
        sa.Column('build_id',sa.String(36),sa.ForeignKey('course_builds.id'),nullable=False),sa.Column('budget_id',sa.String(36),sa.ForeignKey('resource_budgets.id'),nullable=False,unique=True),
        sa.Column('owner_id',sa.String(36),nullable=False),sa.Column('remote_id',sa.String(100)),sa.Column('classroom_id',sa.String(100)),
        sa.Column('status',sa.String(24),nullable=False),sa.Column('next_poll_at',sa.BigInteger,nullable=False),sa.Column('lease_until',sa.BigInteger),
        sa.Column('error_code',sa.String(64)),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_index('ix_openmaic_jobs_workspace_id','openmaic_jobs',['workspace_id'])
    if op.get_bind().dialect.name=='postgresql':
        op.execute('CREATE TRIGGER production_reviews_immutable BEFORE UPDATE OR DELETE ON production_reviews FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()')
        op.execute('CREATE TRIGGER resource_payload_immutable BEFORE UPDATE OF body,sha256,workspace_id,build_id OR DELETE ON resource_artifacts FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()')
    else:
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER production_reviews_no_{action.lower()} BEFORE {action} ON production_reviews BEGIN SELECT RAISE(ABORT, 'append-only record'); END")
        op.execute("CREATE TRIGGER resource_payload_immutable BEFORE UPDATE OF body,sha256,workspace_id,build_id ON resource_artifacts BEGIN SELECT RAISE(ABORT, 'immutable payload'); END")
        op.execute("CREATE TRIGGER resource_payload_no_delete BEFORE DELETE ON resource_artifacts BEGIN SELECT RAISE(ABORT, 'immutable payload'); END")
    if op.get_bind().dialect.name=='postgresql':
        op.execute('CREATE TRIGGER knowledge_documents_immutable BEFORE UPDATE OR DELETE ON knowledge_documents FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()')
    else:
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER knowledge_documents_no_{action.lower()} BEFORE {action} ON knowledge_documents BEGIN SELECT RAISE(ABORT, 'append-only record'); END")


def downgrade():
    for table in ('openmaic_jobs','resource_model_calls','resource_budgets','production_reviews','search_operations','resource_artifacts','course_builds','generation_policy','knowledge_source_states','knowledge_documents'):
        op.drop_table(table)
    op.drop_index('ix_generated_content_versions_workspace_id',table_name='generated_content_versions')
    if op.get_bind().dialect.name!='sqlite':
        op.drop_constraint('fk_generated_content_workspace','generated_content_versions',type_='foreignkey')
    op.drop_column('generated_content_versions','workspace_id')
