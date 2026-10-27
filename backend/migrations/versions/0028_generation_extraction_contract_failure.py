"""Add typed extraction contract validation failure for generation jobs."""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE salesplaybookgenerationfailure "
        "ADD VALUE IF NOT EXISTS 'extraction_contract_error'"
    )


def downgrade() -> None:
    # PostgreSQL enum values are retained to keep already-persisted jobs readable.
    pass
