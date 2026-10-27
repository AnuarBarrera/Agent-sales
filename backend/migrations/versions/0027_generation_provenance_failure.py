"""Add typed provenance validation failure for async generation jobs."""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE salesplaybookgenerationfailure "
        "ADD VALUE IF NOT EXISTS 'provenance_validation_error'"
    )


def downgrade() -> None:
    # PostgreSQL enum values are intentionally not removed during downgrade;
    # dropping one could make persisted job rows unreadable.
    pass
