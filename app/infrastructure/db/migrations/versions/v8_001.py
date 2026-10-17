"""Append-only development content and evaluation reviews."""
from alembic import op
import sqlalchemy as sa

revision = 'v8_001'
down_revision = 'p0_001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('development_review_scopes', sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('revision', sa.Integer, nullable=False))
    op.create_table('development_reviews', sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('scope_id', sa.String(64), sa.ForeignKey('development_review_scopes.id'), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('target_id', sa.String(160), nullable=False), sa.Column('version', sa.String(64), nullable=False),
        sa.Column('conclusion', sa.String(40), nullable=False), sa.Column('note', sa.String(2000), nullable=False),
        sa.Column('reviewer_id', sa.String(36), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('created_at', sa.BigInteger, nullable=False), sa.UniqueConstraint('scope_id', 'revision'))
    op.create_index('ix_development_reviews_scope_id', 'development_reviews', ['scope_id'])
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('CREATE TRIGGER development_reviews_immutable BEFORE UPDATE OR DELETE ON development_reviews FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation()')
    else:
        for action in ('UPDATE', 'DELETE'):
            op.execute(f"CREATE TRIGGER development_reviews_no_{action.lower()} BEFORE {action} ON development_reviews BEGIN SELECT RAISE(ABORT, 'append-only record'); END")


def downgrade():
    op.drop_table('development_reviews')
    op.drop_table('development_review_scopes')
