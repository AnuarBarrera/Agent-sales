from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.calls.schemas import (
    CallRecordCreate,
    CallRecordRead,
    CallRecordUpdate,
    CallUploadResponse,
    FileAssetRead,
    TranscriptAnalysisCreate,
    TranscriptAnalysisRead,
    TranscriptRead,
)
from app.modules.calls.service import CallsService
from app.modules.identity.router import get_current_user_id, get_current_workspace_id

router = APIRouter(prefix="/calls", tags=["Calls"])


@router.post("/records", response_model=CallRecordRead)
def create_call_record(
    call_data: CallRecordCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    user_id: UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Create a new call record."""
    service = CallsService(db)
    call_record = service.create_call_record(
        workspace_id=workspace_id,
        lead_id=call_data.lead_id,
        uploaded_by_id=user_id,
        duration_seconds=call_data.duration_seconds,
        call_date=call_data.call_date,
        notes=call_data.notes,
    )
    return CallRecordRead.model_validate(call_record)


@router.post("/upload", response_model=CallUploadResponse)
def upload_audio(
    workspace_id: UUID = Depends(get_current_workspace_id),
    user_id: UUID = Depends(get_current_user_id),
    lead_id: UUID = Form(...),
    file: UploadFile = File(...),
    duration_seconds: int | None = Form(None),
    call_date: str | None = Form(None),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Upload audio file and create call record."""
    service = CallsService(db)

    # Read file data
    file_data = file.file.read()

    # Parse call_date if provided
    parsed_call_date = None
    if call_date:
        from datetime import datetime
        parsed_call_date = datetime.fromisoformat(call_date.replace("Z", "+00:00"))

    call_record = service.upload_audio(
        workspace_id=workspace_id,
        lead_id=lead_id,
        uploaded_by_id=user_id,
        file_data=file_data,
        filename=file.filename or "audio",
        content_type=file.content_type or "audio/mpeg",
        duration_seconds=duration_seconds,
        call_date=parsed_call_date,
        notes=notes,
    )

    # Get the file asset that was created
    from app.modules.calls.models import FileAsset
    file_asset = db.query(FileAsset).filter(
        FileAsset.call_record_id == call_record.id
    ).first()

    return CallUploadResponse(
        call_record=CallRecordRead.model_validate(call_record),
        file_asset=FileAssetRead.model_validate(file_asset),
    )


@router.post("/{call_record_id}/transcribe", response_model=TranscriptRead)
def transcribe_call(
    call_record_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    """Transcribe an audio call."""
    service = CallsService(db)
    transcript = service.transcribe_call(call_record_id, workspace_id)
    return TranscriptRead.model_validate(transcript)


@router.get("/records", response_model=list[CallRecordRead])
def list_call_records(
    lead_id: UUID | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    """List call records with filters."""
    service = CallsService(db)
    records = service.list_call_records(
        workspace_id=workspace_id,
        lead_id=lead_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [CallRecordRead.model_validate(r) for r in records]


@router.get("/records/{call_id}", response_model=CallRecordRead)
def get_call_record(
    call_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    """Get a call record by ID."""
    service = CallsService(db)
    call_record = service.get_call_record(call_id, workspace_id)
    if not call_record:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Call record not found")
    return CallRecordRead.model_validate(call_record)


@router.patch("/records/{call_id}", response_model=CallRecordRead)
def update_call_record(
    call_id: UUID,
    call_data: CallRecordUpdate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    """Update a call record."""
    service = CallsService(db)
    call_record = service.get_call_record(call_id, workspace_id)
    if not call_record:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Call record not found")

    update_data = call_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(call_record, field, value)

    db.flush()
    return CallRecordRead.model_validate(call_record)


@router.get("/transcripts/{transcript_id}", response_model=TranscriptRead)
def get_transcript(
    transcript_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    """Get a transcript by ID."""
    service = CallsService(db)
    transcript = service.get_transcript(transcript_id, workspace_id)
    if not transcript:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Transcript not found")
    return TranscriptRead.model_validate(transcript)


@router.post("/transcripts/{transcript_id}/analyze", response_model=TranscriptAnalysisRead)
def analyze_transcript(
    transcript_id: UUID,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    """Analyze transcript using AI."""
    service = CallsService(db)
    analysis = service.analyze_transcript(transcript_id, workspace_id)
    return TranscriptAnalysisRead.model_validate(analysis)


@router.post("/transcripts/{transcript_id}/analysis", response_model=TranscriptAnalysisRead)
def create_analysis(
    transcript_id: UUID,
    analysis_data: TranscriptAnalysisCreate,
    workspace_id: UUID = Depends(get_current_workspace_id),
    db: Session = Depends(get_db),
):
    """Create transcript analysis manually."""
    service = CallsService(db)
    analysis = service.create_analysis(
        transcript_id=transcript_id,
        workspace_id=workspace_id,
        summary=analysis_data.summary,
        intent=analysis_data.intent,
        objections=analysis_data.objections,
        needs=analysis_data.needs,
        next_steps=analysis_data.next_steps,
        confidence_score=analysis_data.confidence_score,
        model=analysis_data.model,
    )
    return TranscriptAnalysisRead.model_validate(analysis)
