"""Immutable dynamic content, project curricula and explicit unit bindings."""
from alembic import op
import sqlalchemy as sa

revision = 'g01_001'
down_revision = 'v8_001'
branch_labels = None
depends_on = None
TABLES = ('generated_content_versions', 'task_versions', 'activity_versions', 'curriculum_versions', 'course_bindings')


def upgrade():
    op.create_table('generated_content_versions',
        sa.Column('unit_id', sa.String(100), primary_key=True),
        sa.Column('version', sa.String(64), nullable=False, unique=True),
        sa.Column('spec', sa.JSON, nullable=False), sa.Column('validation', sa.JSON, nullable=False),
        sa.Column('creator_id', sa.String(36), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('created_at', sa.BigInteger, nullable=False))
    op.create_table('task_versions',
        sa.Column('unit_id', sa.String(100), sa.ForeignKey('generated_content_versions.unit_id'), primary_key=True),
        sa.Column('task_id', sa.String(100), primary_key=True), sa.Column('spec', sa.JSON, nullable=False))
    op.create_table('activity_versions',
        sa.Column('unit_id', sa.String(100), primary_key=True),
        sa.Column('parent_unit_id', sa.String(100), sa.ForeignKey('generated_content_versions.unit_id'), nullable=False),
        sa.Column('version', sa.String(64), nullable=False, unique=True), sa.Column('spec', sa.JSON, nullable=False))
    op.create_table('curriculum_versions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('workspace_id', sa.String(36), sa.ForeignKey('workspaces.id'), nullable=False),
        sa.Column('version', sa.String(64), nullable=False), sa.Column('snapshot', sa.JSON, nullable=False),
        sa.Column('created_at', sa.BigInteger, nullable=False))
    op.create_index('ix_curriculum_versions_workspace_id', 'curriculum_versions', ['workspace_id'])
    op.create_table('course_bindings',
        sa.Column('workspace_id', sa.String(36), sa.ForeignKey('workspaces.id'), primary_key=True),
        sa.Column('curriculum_id', sa.String(36), sa.ForeignKey('curriculum_versions.id'), nullable=False),
        sa.Column('active_unit_id', sa.String(100), nullable=False), sa.Column('revision', sa.Integer, nullable=False))
    for name in TABLES[:-1]:
        if op.get_bind().dialect.name == 'postgresql':
            op.execute(f'CREATE TRIGGER {name}_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()')
        else:
            for action in ('UPDATE', 'DELETE'):
                op.execute(f"CREATE TRIGGER {name}_no_{action.lower()} BEFORE {action} ON {name} BEGIN SELECT RAISE(ABORT, 'append-only record'); END")


def downgrade():
    for name in reversed(TABLES):
        op.drop_table(name)
