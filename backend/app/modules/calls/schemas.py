from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CallStatus(str, Enum):
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    FAILED = "failed"


class FileType(str, Enum):
    AUDIO = "audio"
    TRANSCRIPT = "transcript"
    DOCUMENT = "document"


class CallRecordBase(BaseModel):
    lead_id: UUID
    duration_seconds: int | None = None
    call_date: datetime | None = None
    notes: str | None = None


class CallRecordCreate(CallRecordBase):
    pass


class CallRecordUpdate(BaseModel):
    duration_seconds: int | None = None
    call_date: datetime | None = None
    notes: str | None = None
    status: str | None = None


class CallRecordRead(CallRecordBase):
    id: UUID
    workspace_id: UUID
    uploaded_by_id: UUID | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FileAssetBase(BaseModel):
    file_type: str
    filename: str
    content_type: str
    size_bytes: int
    storage_path: str
    checksum: str


class FileAssetCreate(FileAssetBase):
    call_record_id: UUID | None = None


class FileAssetRead(FileAssetBase):
    id: UUID
    workspace_id: UUID
    call_record_id: UUID | None
    uploaded_by_id: UUID | None
    uploaded_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TranscriptBase(BaseModel):
    call_record_id: UUID
    content: str
    language: str | None = None
    duration_seconds: float | None = None


class TranscriptCreate(TranscriptBase):
    file_asset_id: UUID | None = None


class TranscriptUpdate(BaseModel):
    content: str | None = None
    language: str | None = None
    status: str | None = None


class TranscriptRead(TranscriptBase):
    id: UUID
    workspace_id: UUID
    file_asset_id: UUID | None
    duration_seconds: float | None
    status: str
    provider: str | None
    provider_request_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TranscriptAnalysisBase(BaseModel):
    transcript_id: UUID
    summary: str | None = None
    intent: str | None = None
    objections: str | None = None
    needs: str | None = None
    next_steps: str | None = None
    confidence_score: float | None = None
    model: str | None = None


class TranscriptAnalysisCreate(TranscriptAnalysisBase):
    pass


class TranscriptAnalysisRead(TranscriptAnalysisBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CallUploadRequest(BaseModel):
    lead_id: UUID
    file_asset_id: UUID
    duration_seconds: int | None = None
    call_date: datetime | None = None
    notes: str | None = None


class CallUploadResponse(BaseModel):
    call_record: "CallRecordRead"
    file_asset: "FileAssetRead"
