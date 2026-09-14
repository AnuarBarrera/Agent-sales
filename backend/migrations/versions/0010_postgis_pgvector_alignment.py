"""Align the initial text placeholders with the PostGIS and pgvector models."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE prospection_runs
        ALTER COLUMN polygon_geometry TYPE geometry(Polygon, 4326)
        USING CASE
            WHEN polygon_geometry IS NULL OR polygon_geometry = '' THEN NULL
            WHEN polygon_geometry ILIKE 'POLYGON%%' THEN ST_GeomFromText(polygon_geometry, 4326)
            ELSE NULL
        END
        """
    )
    op.execute(
        """
        ALTER TABLE document_chunks
        RENAME COLUMN metadata TO meta
        """
    )
    op.execute(
        """
        ALTER TABLE document_chunks
        ALTER COLUMN embedding TYPE vector(1536)
        USING CASE
            WHEN embedding IS NULL OR embedding = '' THEN NULL
            ELSE embedding::vector
        END
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE document_chunks ALTER COLUMN embedding TYPE text USING embedding::text"
    )
    op.execute("ALTER TABLE document_chunks RENAME COLUMN meta TO metadata")
    op.execute(
        """
        ALTER TABLE prospection_runs
        ALTER COLUMN polygon_geometry TYPE text
        USING CASE WHEN polygon_geometry IS NULL THEN NULL ELSE ST_AsText(polygon_geometry) END
        """
    )
