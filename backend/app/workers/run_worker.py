"""RQ worker extension that logs private build identity before work starts."""

from __future__ import annotations

import logging
from pathlib import Path

from rq import Worker

from app.core.config import get_settings
from app.core.release_identity import process_release_identity
from app.modules.sales_playbook.execution_budget import ExecutionBudgetPolicy

logger = logging.getLogger(__name__)


def emit_worker_startup_identity() -> None:
    settings = get_settings()
    identity = process_release_identity(
        Path(__file__).resolve().parents[2],
        role="worker",
        build_revision=settings.app_build_revision,
        manifest_path=Path("/opt/app-build/build_manifest.json"),
    )
    logger.info("runtime_release_identity", extra={"release_identity": identity})
    logger.info(
        "sales_playbook_execution_budget",
        extra={"execution_budget": ExecutionBudgetPolicy.from_settings(settings).to_dict()},
    )


class AttestingWorker(Worker):
    """Keep RQ's standard worker lifecycle and add a startup attestation."""

    def work(self, *args, **kwargs):
        emit_worker_startup_identity()
        return super().work(*args, **kwargs)
