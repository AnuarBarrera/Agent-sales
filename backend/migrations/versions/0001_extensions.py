"""Enable PostGIS and pgvector extensions

Revision ID: 0001
Revises:
Create Date: 2026-09-12

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable PostGIS extension for geometry/geography types
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # Enable pgvector extension for vector similarity search
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Enable uuid-ossp for UUID generation
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")


def downgrade() -> None:
    # Drop extensions in reverse order
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute("DROP EXTENSION IF EXISTS postgis")
