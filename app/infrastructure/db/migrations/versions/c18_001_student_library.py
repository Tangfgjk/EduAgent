"""Owner-scoped local knowledge documents."""
from alembic import op
import sqlalchemy as sa
revision='c18_001'
down_revision='r14_003'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('student_documents',sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('workspace_id',sa.String(36),sa.ForeignKey('workspaces.id'),nullable=False),
        sa.Column('title',sa.String(120),nullable=False),sa.Column('body',sa.Text,nullable=False),
        sa.Column('sha256',sa.String(64),nullable=False),sa.Column('created_at',sa.BigInteger,nullable=False))
    op.create_index('ix_student_documents_workspace_id','student_documents',['workspace_id'])

def downgrade():
    raise RuntimeError('Forward-only migration; restore a verified backup')
