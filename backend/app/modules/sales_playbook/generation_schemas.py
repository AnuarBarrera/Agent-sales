from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.sales_playbook.generation_models import (
    GenerationFailureCode,
    GenerationJobPhase,
    GenerationJobStatus,
    SalesPlaybookGenerationJob,
)


class SalesPlaybookGenerationJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: GenerationJobStatus
    phase: GenerationJobPhase
    current_step: int
    total_steps: int
    progress_message: str
    result_playbook_id: UUID | None
    failure_code: GenerationFailureCode | None
    failure_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_job(cls, job: SalesPlaybookGenerationJob) -> "SalesPlaybookGenerationJobRead":
        return cls.model_validate(job)
