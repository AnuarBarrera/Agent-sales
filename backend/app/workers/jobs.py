import asyncio
from uuid import UUID

from sqlalchemy import select

# Register all relationship targets before SQLAlchemy configures mappers.
from app.db import models as _models  # noqa: F401
from app.db.session import SessionLocal
from app.modules.prospection.models import RunLead
from app.modules.prospection.service import ProspectionService
from app.modules.validation.service import ValidationService


def execute_prospection_run(run_id: str) -> None:
    """RQ entry point: create an isolated DB session per job."""
    db = SessionLocal()
    try:
        asyncio.run(ProspectionService(db).execute_run(UUID(run_id)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def execute_validation_run(run_id: str) -> None:
    """RQ entry point for validating all leads from a prospection run."""
    db = SessionLocal()
    try:
        lead_ids = list(
            db.execute(
                select(RunLead.lead_id).where(
                    RunLead.run_id == UUID(run_id),
                    RunLead.lead_id.is_not(None),
                )
            ).scalars().all()
        )
        asyncio.run(ValidationService(db).run_validation_job(lead_ids))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
