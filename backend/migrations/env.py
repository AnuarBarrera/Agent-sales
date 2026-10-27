import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add the backend directory to Python path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db.base import Base

# Import all models so Alembic autogenerate sees the complete CRM schema.
from app.modules.activities import models as _activities_models  # noqa: F401
from app.modules.calls import models as _calls_models  # noqa: F401
from app.modules.demo import models as _demo_models  # noqa: F401
from app.modules.identity import models as _identity_models  # noqa: F401
from app.modules.lead_fit import models as _lead_fit_models  # noqa: F401
from app.modules.leads import models as _leads_models  # noqa: F401
from app.modules.notifications import models as _notifications_models  # noqa: F401
from app.modules.outreach import models as _outreach_models  # noqa: F401
from app.modules.pipeline import models as _pipeline_models  # noqa: F401
from app.modules.prospect_analysis import models as _prospect_analysis_models  # noqa: F401
from app.modules.prospect_research import models as _prospect_research_models  # noqa: F401
from app.modules.prospection import models as _prospection_models  # noqa: F401
from app.modules.rag import models as _rag_models  # noqa: F401
from app.modules.sales_playbook import (
    document_models as _sales_playbook_document_models,  # noqa: F401
)
from app.modules.sales_playbook import (
    generation_models as _sales_playbook_generation_models,  # noqa: F401
)
from app.modules.sales_playbook import models as _sales_playbook_models  # noqa: F401
from app.modules.seller_profile import models as _seller_profile_models  # noqa: F401
from app.modules.validation import models as _validation_models  # noqa: F401

config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Get database URL from settings (only if not already set by test)
if not config.get_main_option("sqlalchemy.url"):
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
