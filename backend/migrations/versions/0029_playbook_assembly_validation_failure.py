"""Add a typed final SalesPlaybook assembly validation failure."""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE salesplaybookgenerationfailure "
        "ADD VALUE IF NOT EXISTS 'playbook_assembly_validation_error'"
    )


def downgrade() -> None:
    # Retain enum values so historical GenerationJob rows remain readable.
    pass
