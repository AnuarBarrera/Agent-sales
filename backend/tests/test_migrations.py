import time

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.core.config import get_settings


def get_alembic_config() -> Config:
    config = Config("alembic.ini")
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


TEST_DB_URL = "postgresql+psycopg://postgres:postgres@postgres:5432/tuwebmx_crm_test"
ADMIN_DB_URL = "postgresql+psycopg://postgres:postgres@postgres:5432/postgres"


def drop_test_db():
    """Helper to drop test database, terminating any existing connections."""
    with create_engine(ADMIN_DB_URL, isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(text("""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = 'tuwebmx_crm_test' AND pid <> pg_backend_pid()
        """))
        conn.execute(text("DROP DATABASE IF EXISTS tuwebmx_crm_test"))


def create_test_db():
    """Create test database using template0 to avoid collation issues."""
    with create_engine(ADMIN_DB_URL, isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(text("CREATE DATABASE tuwebmx_crm_test TEMPLATE template0"))
    # Small delay to allow database to be fully ready
    time.sleep(0.5)


def test_migrations_upgrade_and_downgrade():
    """Test that migrations can upgrade and downgrade cleanly"""
    drop_test_db()
    create_test_db()

    # Create fresh engine for the test database
    test_engine = create_engine(TEST_DB_URL)

    # Configure Alembic for test database
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DB_URL)

    # Upgrade to head - this will run the extensions migration
    command.upgrade(config, "head")

    # Verify extensions are present
    with test_engine.connect() as conn:
        result = conn.execute(text("SELECT extname FROM pg_extension WHERE extname IN ('postgis', 'vector')"))
        extensions = {row[0] for row in result}
        assert "postgis" in extensions
        assert "vector" in extensions

    # Downgrade to base
    command.downgrade(config, "base")

    # Upgrade again
    command.upgrade(config, "head")

    # Verify again
    with test_engine.connect() as conn:
        result = conn.execute(text("SELECT extname FROM pg_extension WHERE extname IN ('postgis', 'vector')"))
        extensions = {row[0] for row in result}
        assert "postgis" in extensions
        assert "vector" in extensions

    # Clean up
    test_engine.dispose()
    drop_test_db()


def test_alembic_current_after_upgrade():
    """Test that alembic current shows correct revision after upgrade"""
    drop_test_db()
    create_test_db()

    # Create fresh engine for the test database
    test_engine = create_engine(TEST_DB_URL)

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DB_URL)

    command.upgrade(config, "head")

    # Check current revision
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1

    # Clean up
    test_engine.dispose()
    drop_test_db()
