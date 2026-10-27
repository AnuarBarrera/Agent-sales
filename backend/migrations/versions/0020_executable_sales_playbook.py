"""Mark executable sales playbook content versions.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sales_playbooks", sa.Column("content_contract_version", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("sales_playbooks", "content_contract_version")
