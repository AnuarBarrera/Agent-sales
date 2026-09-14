import logging
import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.meta import MetaAdsProvider, MetaAdsProviderError
from app.integrations.places import GooglePlacesProvider, PlacesProviderError
from app.integrations.website import WebsiteChecker, WebsiteCheckerError
from app.modules.leads.models import Lead, LeadProfile, ValidationStatus
from app.modules.validation.models import (
    LeadValidation,
    ValidationResult,
    ValidationSnapshot,
)

logger = logging.getLogger(__name__)


class ValidationService:
    def __init__(self, db: Session):
        self.db = db
        self.website_checker = WebsiteChecker()
        self.places_provider = GooglePlacesProvider()
        self.meta_ads_provider = MetaAdsProvider()

    def classify_lead(self, lead_id: UUID, max_retries: int = 2) -> tuple[LeadProfile | None, int | None, str | None]:
        """
        Classify a lead based on validation signals.

        Returns (profile, score, score_reason)
        """
        # Get latest validation snapshot
        snapshot = self.db.execute(
            select(ValidationSnapshot).where(ValidationSnapshot.lead_id == lead_id)
        ).scalar_one_or_none()

        if not snapshot:
            return LeadProfile.UNCLASSIFIED, None, "No validation data available"

        # Classification logic based on signals
        # Priority: ORO > AGENCIA > INQUILINO > FRIO

        # ORO: Has website + active Google presence + active Meta Ads
        if (snapshot.website_result == ValidationResult.VALID and
            snapshot.google_presence_result == ValidationResult.ACTIVE and
            snapshot.meta_ads_result == ValidationResult.ACTIVE):
            return LeadProfile.ORO, 90, "Strong online presence across all channels"

        # AGENCIA: Has website + Google presence (may not have Meta Ads)
        if (snapshot.website_result == ValidationResult.VALID and
            snapshot.google_presence_result in (ValidationResult.ACTIVE, ValidationResult.VALID)):
            return LeadProfile.AGENCIA, 70, "Professional online presence"

        # INQUILINO: Google presence but no website
        if (snapshot.google_presence_result in (ValidationResult.ACTIVE, ValidationResult.VALID) and
            snapshot.website_result in (ValidationResult.NONE, ValidationResult.INVALID, ValidationResult.NOT_FOUND)):
            return LeadProfile.INQUILINO, 50, "Google presence without website"

        # FRIO: No usable signals or all errors
        if (snapshot.website_result in (ValidationResult.ERROR, ValidationResult.NOT_FOUND) and
            snapshot.google_presence_result in (ValidationResult.ERROR, ValidationResult.NOT_FOUND)):
            return LeadProfile.FRIO, 10, "No usable online signals"

        # Default to UNCLASSIFIED if signals are partial
        return LeadProfile.UNCLASSIFIED, None, "Partial validation results"

    async def run_validation_job(
        self,
        lead_ids: list[UUID],
        check_types: list[str] | None = None,
        max_retries: int = 2
    ) -> dict[str, int]:
        """
        Run validation for a batch of leads.

        Returns counts: {processed, successful, failed, partial}
        """
        if check_types is None:
            check_types = ["website", "google_presence", "meta_ads"]

        processed = 0
        successful = 0
        failed = 0
        partial = 0

        for lead_id in lead_ids:
            lead = self.db.get(Lead, lead_id)
            if not lead:
                logger.warning(f"Lead {lead_id} not found, skipping")
                failed += 1
                continue

            try:
                lead.validation_status = ValidationStatus.RUNNING
                self.db.flush()

                # Run validations
                results: dict[str, LeadValidation] = {}
                for check_type in check_types:
                    result = await self._run_single_validation(lead, check_type, max_retries)
                    results[check_type] = result

                # Update lead with results
                self._update_lead_validation(lead, results)

                # Create or update snapshot
                self._update_snapshot(lead, results)

                # Classify
                profile, score, score_reason = self.classify_lead(lead.id)
                if profile != LeadProfile.UNCLASSIFIED:
                    lead.profile = profile  # type: ignore[assignment]
                    lead.score = score
                    lead.score_reason = score_reason

                lead.validation_status = ValidationStatus.COMPLETE
                self.db.flush()

                processed += 1
                if all(r.result == ValidationResult.ERROR for r in results.values()):
                    failed += 1
                elif any(r.result == ValidationResult.ERROR for r in results.values()):
                    partial += 1
                else:
                    successful += 1

            except Exception as e:
                logger.exception(f"Validation failed for lead {lead_id}: {e}")
                lead.validation_status = ValidationStatus.ERROR
                self.db.flush()
                failed += 1

        return {
            "processed": processed,
            "successful": successful,
            "failed": failed,
            "partial": partial,
        }

    async def _run_single_validation(
        self,
        lead: Lead,
        check_type: str,
        max_retries: int = 2
    ) -> LeadValidation:
        """Run a single validation check with retries."""
        attempt = 0
        last_error = None

        while attempt < max_retries:
            attempt += 1
            start_time = time.time()

            try:
                if check_type == "website":
                    result, detail, provider_req_id = await self._check_website(lead)
                elif check_type == "google_presence":
                    result, detail, provider_req_id = await self._check_google_presence(lead)
                elif check_type == "meta_ads":
                    result, detail, provider_req_id = await self._check_meta_ads(lead)
                else:
                    raise ValueError(f"Unknown check type: {check_type}")

                duration_ms = int((time.time() - start_time) * 1000)

                validation = LeadValidation(
                    lead_id=lead.id,
                    check_type=check_type,
                    result=result,
                    detail=detail,
                    provider=check_type,
                    provider_request_id=provider_req_id,
                    duration_ms=duration_ms,
                    attempt=attempt,
                )
                self.db.add(validation)
                self.db.flush()
                return validation

            except Exception as e:
                last_error = e
                logger.warning(f"Validation attempt {attempt} failed for lead {lead.id} ({check_type}): {e}")
                if attempt >= max_retries:
                    break
                time.sleep(1)  # Brief pause before retry

        # All retries failed
        duration_ms = int((time.time() - start_time) * 1000)
        validation = LeadValidation(
            lead_id=lead.id,
            check_type=check_type,
            result=ValidationResult.ERROR,
            detail=str(last_error),
            provider=check_type,
            provider_request_id=None,
            duration_ms=duration_ms,
            attempt=attempt,
        )
        self.db.add(validation)
        self.db.flush()
        return validation

    async def _check_website(self, lead: Lead) -> tuple[ValidationResult, str | None, str | None]:
        """Check website availability."""
        if not lead.business or not lead.business.website:
            return ValidationResult.NONE, "No website URL provided", None

        try:
            result = await self.website_checker.check(lead.business.website)
            if result.is_valid:
                return ValidationResult.VALID, f"Website accessible (status: {result.status_code})", result.request_id
            else:
                return ValidationResult.INVALID, f"Website returned error: {result.error}", result.request_id
        except WebsiteCheckerError as e:
            return ValidationResult.ERROR, str(e), None

    async def _check_google_presence(self, lead: Lead) -> tuple[ValidationResult, str | None, str | None]:
        """Check Google Places presence."""
        if not lead.business or not lead.business.place_id:
            return ValidationResult.NONE, "No Google Place ID", None

        try:
            place = await self.places_provider.details(lead.business.place_id)
            if place and place.place_id:
                return ValidationResult.ACTIVE, f"Google Places listing found: {place.name}", place.place_id
            return ValidationResult.NOT_FOUND, "Place not found in Google Places", None
        except PlacesProviderError as e:
            return ValidationResult.ERROR, str(e), None

    async def _check_meta_ads(self, lead: Lead) -> tuple[ValidationResult, str | None, str | None]:
        """Check Meta Ads activity."""
        if not lead.business or not lead.business.name:
            return ValidationResult.NONE, "No business name for Meta Ads check", None

        try:
            ads = await self.meta_ads_provider.search_active_ads(lead.business.name)
            if ads:
                return ValidationResult.ACTIVE, f"Found {len(ads)} active ads", None
            return ValidationResult.NONE, "No active Meta Ads found", None
        except MetaAdsProviderError as e:
            return ValidationResult.ERROR, str(e), None

    def _update_lead_validation(self, lead: Lead, results: dict[str, LeadValidation]) -> None:
        """Update lead with validation results."""
        for check_type, _validation in results.items():
            if check_type == "website":
                lead.website_checked_at = datetime.now(UTC)
            elif check_type == "google_presence":
                lead.google_presence_checked_at = datetime.now(UTC)
            elif check_type == "meta_ads":
                lead.meta_ads_checked_at = datetime.now(UTC)

    def _update_snapshot(self, lead: Lead, results: dict[str, LeadValidation]) -> None:
        """Update or create validation snapshot for the lead."""
        snapshot = self.db.execute(
            select(ValidationSnapshot).where(ValidationSnapshot.lead_id == lead.id)
        ).scalar_one_or_none()

        if not snapshot:
            snapshot = ValidationSnapshot(lead_id=lead.id)
            self.db.add(snapshot)

        for check_type, validation in results.items():
            if check_type == "website":
                snapshot.website_result = validation.result
            elif check_type == "google_presence":
                snapshot.google_presence_result = validation.result
            elif check_type == "meta_ads":
                snapshot.meta_ads_result = validation.result

        # Update profile and score
        profile, score, score_reason = self.classify_lead(lead.id)
        if profile != LeadProfile.UNCLASSIFIED:
            snapshot.profile = profile  # type: ignore[assignment]
            snapshot.score = score
            snapshot.score_reason = score_reason

        self.db.flush()
