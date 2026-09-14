import hashlib
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.storage import StorageProvider
from app.integrations.transcription import TranscriptionProvider
from app.modules.calls.models import (
    CallRecord,
    CallStatus,
    FileAsset,
    Transcript,
    TranscriptAnalysis,
)

logger = logging.getLogger(__name__)


class CallsService:
    def __init__(self, db: Session):
        self.db = db
        self.storage = StorageProvider()
        self.transcription = TranscriptionProvider()

    def _require_lead(self, workspace_id: UUID, lead_id: UUID) -> None:
        """Ensure a call cannot be attached across workspaces."""
        from app.modules.leads.models import Lead

        lead = self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if not lead:
            raise ValueError("Lead not found")

    def create_call_record(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        uploaded_by_id: UUID | None = None,
        duration_seconds: int | None = None,
        call_date: datetime | None = None,
        notes: str | None = None,
    ) -> CallRecord:
        """Create a call record."""
        self._require_lead(workspace_id, lead_id)
        call_record = CallRecord(
            workspace_id=workspace_id,
            lead_id=lead_id,
            uploaded_by_id=uploaded_by_id,
            duration_seconds=duration_seconds,
            call_date=call_date,
            notes=notes,
            status=CallStatus.UPLOADED,
        )
        self.db.add(call_record)
        self.db.flush()
        return call_record

    def upload_file(
        self,
        workspace_id: UUID,
        uploaded_by_id: UUID,
        call_record_id: UUID | None,
        file_data: bytes,
        filename: str,
        content_type: str,
        file_type: str,
    ) -> FileAsset:
        """Upload a file to storage and create FileAsset record."""
        # Calculate checksum
        checksum = hashlib.sha256(file_data).hexdigest()

        # Generate storage path
        timestamp = datetime.now(UTC).strftime("%Y/%m/%d")
        storage_path = f"{content_type.split('/')[0]}/{timestamp}/{checksum[:16]}_{filename}"

        # Upload to storage
        self.storage.upload(
            key=storage_path,
            data=file_data,
            content_type=content_type,
        )

        # Create file asset record
        file_asset = FileAsset(
            workspace_id=workspace_id,
            call_record_id=call_record_id,
            uploaded_by_id=uploaded_by_id,
            file_type=file_type,
            filename=filename,
            content_type=content_type,
            size_bytes=len(file_data),
            storage_path=storage_path,
            checksum=checksum,
        )
        self.db.add(file_asset)
        self.db.flush()
        return file_asset

    def upload_audio(
        self,
        workspace_id: UUID,
        lead_id: UUID,
        uploaded_by_id: UUID,
        file_data: bytes,
        filename: str,
        content_type: str,
        duration_seconds: int | None = None,
        call_date: datetime | None = None,
        notes: str | None = None,
    ) -> CallRecord:
        """Upload audio file and create call record."""
        # Create call record
        call_record = self.create_call_record(
            workspace_id=workspace_id,
            lead_id=lead_id,
            uploaded_by_id=uploaded_by_id,
            duration_seconds=duration_seconds,
            call_date=call_date,
            notes=notes,
        )

        # Upload audio file
        self.upload_file(
            workspace_id=workspace_id,
            uploaded_by_id=uploaded_by_id,
            call_record_id=call_record.id,
            file_data=file_data,
            filename=filename,
            content_type=content_type,
            file_type="audio",
        )

        # Update call record status
        call_record.status = CallStatus.TRANSCRIBING
        self.db.flush()

        return call_record

    def transcribe_call(
        self,
        call_record_id: UUID,
        workspace_id: UUID,
    ) -> Transcript:
        """Transcribe an audio call."""
        call_record = self.db.get(CallRecord, call_record_id)
        if not call_record or call_record.workspace_id != workspace_id:
            raise ValueError("Call record not found")

        # Get audio file asset
        audio_file = self.db.execute(
            select(FileAsset).where(
                FileAsset.call_record_id == call_record_id,
                FileAsset.file_type == "audio",
            )
        ).scalar_one_or_none()

        if not audio_file:
            raise ValueError("Audio file not found")

        # Update status
        call_record.status = CallStatus.TRANSCRIBING
        self.db.flush()

        # Transcribe
        try:
            result = self.transcription.transcribe(
                audio_data=self.storage.download(audio_file.storage_path),
                content_type=audio_file.content_type,
                filename=audio_file.filename,
            )

            # Create transcript
            transcript = Transcript(
                workspace_id=workspace_id,
                call_record_id=call_record_id,
                file_asset_id=audio_file.id,
                content=result.get("text", ""),
                language=result.get("language"),
                duration_seconds=result.get("duration"),
                status=CallStatus.TRANSCRIBED,
                provider=result.get("provider"),
                provider_request_id=result.get("request_id"),
            )
            self.db.add(transcript)

            # Update call record status
            call_record.status = CallStatus.TRANSCRIBED
            self.db.flush()

            return transcript

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            call_record.status = CallStatus.FAILED
            self.db.flush()
            raise

    def get_call_record(self, call_id: UUID, workspace_id: UUID) -> CallRecord | None:
        """Get a call record by ID."""
        return self.db.execute(
            select(CallRecord).where(
                CallRecord.id == call_id,
                CallRecord.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def list_call_records(
        self,
        workspace_id: UUID,
        lead_id: UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CallRecord]:
        """List call records with filters."""
        query = select(CallRecord).where(CallRecord.workspace_id == workspace_id)

        if lead_id:
            query = query.where(CallRecord.lead_id == lead_id)
        if status:
            query = query.where(CallRecord.status == status)

        query = query.order_by(CallRecord.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.execute(query).scalars().all())

    def get_transcript(self, transcript_id: UUID, workspace_id: UUID) -> Transcript | None:
        """Get a transcript by ID."""
        return self.db.execute(
            select(Transcript).where(
                Transcript.id == transcript_id,
                Transcript.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def create_analysis(
        self,
        transcript_id: UUID,
        workspace_id: UUID,
        summary: str | None = None,
        intent: str | None = None,
        objections: str | None = None,
        needs: str | None = None,
        next_steps: str | None = None,
        confidence_score: float | None = None,
        model: str | None = None,
    ) -> TranscriptAnalysis:
        """Create transcript analysis."""
        from app.modules.calls.models import TranscriptAnalysis

        analysis = TranscriptAnalysis(
            transcript_id=transcript_id,
            workspace_id=workspace_id,
            summary=summary,
            intent=intent,
            objections=objections,
            needs=needs,
            next_steps=next_steps,
            confidence_score=confidence_score,
            model=model,
        )
        self.db.add(analysis)
        self.db.flush()
        return analysis

    def get_analysis(self, analysis_id: UUID, workspace_id: UUID) -> TranscriptAnalysis | None:
        """Get transcript analysis by ID."""
        from app.modules.calls.models import TranscriptAnalysis
        return self.db.execute(
            select(TranscriptAnalysis).where(
                TranscriptAnalysis.id == analysis_id,
                TranscriptAnalysis.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def analyze_transcript(
        self,
        transcript_id: UUID,
        workspace_id: UUID,
    ) -> TranscriptAnalysis:
        """Analyze transcript using AI."""
        from app.integrations.llm import LLMProvider
        from app.modules.calls.models import TranscriptAnalysis

        transcript = self.get_transcript(transcript_id, workspace_id)
        if not transcript:
            raise ValueError("Transcript not found")

        # Build analysis prompt
        prompt = f"""
        Analiza la siguiente transcripción de una llamada comercial y extrae:
        1. Resumen ejecutivo (máx 200 palabras)
        2. Intención del prospecto
        3. Objeciones mencionadas
        4. Necesidades detectadas
        5. Próximos pasos recomendados
        6. Nivel de confianza (0-1)

        Transcripción:
        {transcript.content}
        """

        llm = LLMProvider()
        result = llm.generate(prompt)

        analysis = TranscriptAnalysis(
            transcript_id=transcript_id,
            workspace_id=workspace_id,
            summary=result.get("summary"),
            intent=result.get("intent"),
            objections=result.get("objections"),
            needs=result.get("needs"),
            next_steps=result.get("next_steps"),
            confidence_score=result.get("confidence"),
            model=result.get("model", "unknown"),
        )
        self.db.add(analysis)
        self.db.flush()
        return analysis
