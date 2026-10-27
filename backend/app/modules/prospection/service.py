import json
import logging
import math
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import require_member
from app.integrations.geocoding import GeoPoint, GoogleGeocodingProvider
from app.integrations.places import GooglePlacesProvider, PlaceCandidate, PlacesQuery
from app.modules.identity.models import Membership, MembershipStatus, UserRole
from app.modules.lead_fit.contracts import FitPolicyContent
from app.modules.lead_fit.models import LeadFitEvaluationStatus, LeadFitPolicy
from app.modules.leads.models import Business, Lead, LeadProfile, ValidationStatus
from app.modules.prospection.models import (
    ProspectionRun,
    ProspectionRunStatus,
    RunEvent,
    RunLead,
    RunLeadResult,
)
from app.modules.prospection.schemas import ProspectionRunCreate

logger = logging.getLogger(__name__)

_INITIAL_ENRICHMENT_BUDGET = {
    "version": 1,
    "max_search_calls": 1,
    "max_extract_calls": 1,
    "max_sources": 3,
}


class ProspectionEvaluationPersistenceError(RuntimeError):
    """Lead Fit evaluation persistence failed; the request must not appear successful."""


class ProspectionService:
    def __init__(
        self,
        db: Session,
        *,
        places_provider=None,
        geocoding_provider=None,
        web_provider=None,
        evaluation_service_factory=None,
        enrichment_service_factory=None,
        fit_queue=None,
    ):
        self.db = db
        self.geocoding_provider = geocoding_provider
        self.places_provider = places_provider
        self.web_provider = web_provider
        self.evaluation_service_factory = evaluation_service_factory
        self.enrichment_service_factory = enrichment_service_factory
        self.fit_queue = fit_queue

    def create_run(self, user_id: UUID, workspace_id: UUID, run_data: ProspectionRunCreate) -> ProspectionRun:
        """Create a new prospection run."""
        require_member(self.db, user_id, workspace_id)
        if run_data.discovery_strategy not in {None, "maps"}:
            raise ValueError("New Lead Fit discovery supports Maps only")
        if run_data.enrich_candidates:
            if run_data.discovery_strategy != "maps" or run_data.candidate_limit > 3:
                raise ValueError("Initial enrichment requires explicit Maps discovery capped at three candidates")
            settings = get_settings()
            if not settings.tavily_enabled or not settings.tavily_api_key:
                raise ValueError("Initial enrichment requires Tavily to be enabled and configured")

        policy = None
        search_plan_snapshot = None
        if run_data.discovery_strategy is not None:
            from app.modules.lead_fit.service import LeadFitPolicyService

            requested_policy = self.db.execute(select(LeadFitPolicy).where(
                LeadFitPolicy.workspace_id == workspace_id,
                LeadFitPolicy.id == run_data.fit_policy_id,
            )).scalar_one_or_none()
            if requested_policy is None:
                raise ValueError("Approved Lead Fit policy not found")
            policy = LeadFitPolicyService(self.db).current(workspace_id, requested_policy.playbook_id)
            if policy is None or policy.id != requested_policy.id:
                raise ValueError("Approved Lead Fit policy is stale")
            search_plan_snapshot = {
                "strategy": run_data.discovery_strategy,
                "query": run_data.search_query.strip(),
                "candidate_limit": run_data.candidate_limit,
                "policy_id": str(policy.id),
                "policy_digest": policy.policy_digest,
            }
            if run_data.enrich_candidates:
                search_plan_snapshot["enrichment"] = dict(_INITIAL_ENRICHMENT_BUDGET)
            from app.modules.lead_fit.contracts import canonical_sha256

            search_plan_snapshot["plan_digest"] = canonical_sha256(search_plan_snapshot)

        run = ProspectionRun(
            workspace_id=workspace_id,
            created_by_id=user_id,
            giro=run_data.giro,
            location_label=run_data.location_label or "web",
            center_latitude=run_data.center_latitude,
            center_longitude=run_data.center_longitude,
            radius_km=run_data.radius_km,
            polygon_geometry=(
                WKTElement(self._geometry_to_wkt(run_data.polygon_geometry), srid=4326)
                if run_data.polygon_geometry
                else None
            ),
            status=ProspectionRunStatus.QUEUED,
            provider="google_places",
            fit_policy_id=policy.id if policy else None,
            search_plan_snapshot=search_plan_snapshot,
        )
        self.db.add(run)
        self.db.flush()
        self._emit_event(run.id, "prospection.run.created", {"status": run.status.value})
        return run

    def get_run(self, run_id: UUID, workspace_id: UUID) -> ProspectionRun | None:
        """Get a prospection run by ID."""
        return self.db.execute(
            select(ProspectionRun).where(
                ProspectionRun.id == run_id,
                ProspectionRun.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def list_runs(self, workspace_id: UUID, status: ProspectionRunStatus | None = None, limit: int = 50, offset: int = 0) -> list[ProspectionRun]:
        """List prospection runs for a workspace."""
        query = select(ProspectionRun).where(ProspectionRun.workspace_id == workspace_id)
        if status:
            query = query.where(ProspectionRun.status == status)
        query = query.order_by(ProspectionRun.requested_at.desc()).limit(limit).offset(offset)
        return list(self.db.execute(query).scalars().all())

    def get_run_events(self, run_id: UUID, workspace_id: UUID) -> list[RunEvent]:
        """Get events for a prospection run."""
        run = self.get_run(run_id, workspace_id)
        if not run:
            return []
        return list(run.run_events)

    def _emit_event(self, run_id: UUID, event_type: str, payload: dict) -> None:
        """Emit a run event."""
        event = RunEvent(
            run_id=run_id,
            event_type=event_type,
            payload=payload,
        )
        self.db.add(event)
        self.db.flush()

    async def resolve_location(self, run: ProspectionRun) -> GeoPoint:
        """Resolve the location label to coordinates."""
        if self.geocoding_provider is None:
            self.geocoding_provider = GoogleGeocodingProvider()
        return await self.geocoding_provider.resolve(run.location_label)

    async def search_places(self, run: ProspectionRun) -> list[PlaceCandidate]:
        """Search for places using the configured provider."""
        if self.places_provider is None:
            self.places_provider = GooglePlacesProvider()
        radius_km = run.radius_km
        if run.polygon_geometry:
            # Google Nearby Search is circle-based. Use a circle that covers the
            # polygon's bounding box, then apply the exact point-in-polygon
            # filter before persistence.
            polygon = self._polygon_coordinates(run.polygon_geometry)
            if polygon:
                latitude_scale = 111.32
                longitude_scale = max(math.cos(math.radians(run.center_latitude)), 0.1) * latitude_scale
                radius_km = min(
                    50.0,
                    max(
                        0.1,
                        max(
                            math.hypot(
                                (lat - run.center_latitude) * latitude_scale,
                                (lng - run.center_longitude) * longitude_scale,
                            )
                            for lng, lat in polygon
                        ),
                    ),
                )
        query = PlacesQuery(
            giro=(run.search_plan_snapshot or {}).get("query", run.giro),
            location_label=run.location_label,
            center_latitude=run.center_latitude,
            center_longitude=run.center_longitude,
            radius_km=radius_km,
            polygon_geometry=run.polygon_geometry,
            max_results=(run.search_plan_snapshot or {}).get("candidate_limit"),
        )
        return await self.places_provider.search(query)

    @staticmethod
    def _geometry_to_wkt(geometry: dict | str) -> str:
        """Normalize browser GeoJSON into PostGIS-compatible WKT."""
        if isinstance(geometry, dict):
            coordinates = geometry.get("coordinates", [[]])[0]
        else:
            value = geometry.strip()
            if value.upper().startswith("POLYGON"):
                return value
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("polygon_geometry must be GeoJSON or WKT") from exc
            coordinates = parsed.get("coordinates", [[]])[0]

        if len(coordinates) < 3:
            raise ValueError("A polygon requires at least three points")
        points = [(float(point[0]), float(point[1])) for point in coordinates]
        if points[0] != points[-1]:
            points.append(points[0])
        rendered = ", ".join(f"{lng} {lat}" for lng, lat in points)
        return f"POLYGON(({rendered}))"

    @staticmethod
    def _polygon_coordinates(polygon: object) -> list[tuple[float, float]]:
        """Read coordinates from a WKTElement, WKT string or GeoJSON value."""
        raw = getattr(polygon, "desc", polygon)
        if isinstance(raw, bytes):
            raw = raw.decode()
        value = str(raw).strip()
        if value.startswith("{"):
            parsed = json.loads(value)
            return [(float(point[0]), float(point[1])) for point in parsed["coordinates"][0]]
        match = re.search(r"POLYGON\s*\(\((.*?)\)\)", value, flags=re.IGNORECASE)
        if not match:
            return []
        return [
            (float(pair.strip().split()[0]), float(pair.strip().split()[1]))
            for pair in match.group(1).split(",")
        ]

    def _is_point_in_polygon(self, place: PlaceCandidate, polygon_wkt: object) -> bool:
        """Check a candidate with a point-in-polygon test before persistence."""
        if place.latitude is None or place.longitude is None:
            return False
        polygon = self._polygon_coordinates(polygon_wkt)
        if len(polygon) < 3:
            return False

        x, y = place.longitude, place.latitude
        inside = False
        for index, (x1, y1) in enumerate(polygon):
            x2, y2 = polygon[(index + 1) % len(polygon)]
            intersects = ((y1 > y) != (y2 > y)) and (
                x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
            )
            if intersects:
                inside = not inside
        return inside

    def _deduplicate_place(self, place: PlaceCandidate, workspace_id: UUID) -> tuple[Business, bool]:
        """Deduplicate a place by place_id within workspace.

        Returns (business, is_new)
        """
        existing = self.db.execute(
            select(Business).where(
                Business.workspace_id == workspace_id,
                Business.place_id == place.place_id,
            )
        ).scalar_one_or_none()

        if existing:
            # Update existing business with new data if needed
            existing.name = place.name
            existing.address = place.address
            existing.phone = place.phone
            existing.website = place.website
            existing.rating = place.rating
            existing.reviews_total = place.reviews_total
            existing.latitude = place.latitude
            existing.longitude = place.longitude
            existing.raw_provider_data = place.raw_provider_data
            existing.provider = "google_places"
            existing.source_key = place.place_id
            existing.updated_at = datetime.now(UTC)
            return existing, False

        # Create new business
        business = Business(
            workspace_id=workspace_id,
            place_id=place.place_id,
            provider="google_places",
            source_key=place.place_id,
            name=place.name,
            address=place.address,
            phone=place.phone,
            website=place.website,
            rating=place.rating,
            reviews_total=place.reviews_total,
            latitude=place.latitude,
            longitude=place.longitude,
            raw_provider_data=place.raw_provider_data,
        )
        self.db.add(business)
        self.db.flush()
        return business, True

    def _create_or_update_lead(self, business: Business, run: ProspectionRun, is_new_business: bool) -> tuple[Lead, RunLeadResult]:
        """Create or update a lead for the business."""
        existing_lead = self.db.execute(
            select(Lead).where(
                Lead.workspace_id == run.workspace_id,
                Lead.business_id == business.id,
            )
        ).scalar_one_or_none()

        if existing_lead:
            # Update source run if not set
            if not existing_lead.source_run_id:
                existing_lead.source_run_id = run.id
            return existing_lead, RunLeadResult.DUPLICATE if not is_new_business else RunLeadResult.UPDATED

        # Create new lead
        setter_id = run.created_by_id
        closer_id = None
        if run.fit_policy_id is not None:
            creator_role = self.db.execute(select(Membership.role).where(
                Membership.workspace_id == run.workspace_id,
                Membership.user_id == run.created_by_id,
                Membership.status == MembershipStatus.ACTIVE,
            )).scalar_one_or_none()
            if creator_role == UserRole.CLOSER:
                setter_id, closer_id = None, run.created_by_id
        lead = Lead(
            workspace_id=run.workspace_id,
            business_id=business.id,
            source_run_id=run.id,
            setter_id=setter_id,
            closer_id=closer_id,
            profile=LeadProfile.UNCLASSIFIED,
            validation_status=ValidationStatus.PENDING,
        )
        from app.modules.pipeline.service import PipelineService

        default_stages = PipelineService(self.db).get_default_stages(run.workspace_id)
        new_stage = next((stage for stage in default_stages if stage.key == "new"), default_stages[0])
        lead.pipeline_stage_id = new_stage.id
        self.db.add(lead)
        self.db.flush()
        return lead, RunLeadResult.NEW

    async def execute_run(self, run_id: UUID) -> None:
        """Execute a complete prospection run."""
        run = self.db.get(ProspectionRun, run_id)
        if not run:
            raise ValueError(f"Run {run_id} not found")
        if run.completed_at and run.status in {
            ProspectionRunStatus.READY,
            ProspectionRunStatus.PARTIAL,
            ProspectionRunStatus.FAILED,
        }:
            return
        if run.fit_policy_id is not None:
            await self._execute_lead_fit_discovery(run)
            return

        try:
            # Stage 1: Searching
            run.status = ProspectionRunStatus.SEARCHING
            run.started_at = datetime.now(UTC)
            self._emit_event(run.id, "prospection.search.started", {})
            self.db.flush()

            # Resolve location if needed
            if run.center_latitude is None and run.center_longitude is None:
                geo = await self.resolve_location(run)
                run.center_latitude = geo.latitude
                run.center_longitude = geo.longitude
                self.db.flush()

            # Search for places
            places = await self.search_places(run)
            run.found_count = len(places)
            self._emit_event(run.id, "prospection.search.completed", {"found_count": run.found_count})
            self.db.flush()

            # Stage 2: Enriching
            run.status = ProspectionRunStatus.ENRICHING
            self._emit_event(run.id, "prospection.enrichment.started", {})
            self.db.flush()

            # Stage 3: Deduplicating
            run.status = ProspectionRunStatus.DEDUPLICATING
            self._emit_event(run.id, "prospection.dedup.started", {})
            self.db.flush()

            new_count = 0
            duplicate_count = 0
            error_count = 0

            for place in places:
                try:
                    # Filter by polygon if provided
                    if run.polygon_geometry and not self._is_point_in_polygon(place, run.polygon_geometry):
                        continue

                    business, is_new = self._deduplicate_place(place, run.workspace_id)
                    lead, result = self._create_or_update_lead(business, run, is_new)

                    # Record run_lead
                    run_lead = RunLead(
                        workspace_id=run.workspace_id,
                        run_id=run.id,
                        lead_id=lead.id,
                        place_id=place.place_id,
                        provider="google_places",
                        source_key=place.place_id,
                        result=result,
                        raw_result=place.raw_provider_data,
                    )
                    self.db.add(run_lead)

                    if result == RunLeadResult.NEW:
                        new_count += 1
                    elif result in (RunLeadResult.DUPLICATE, RunLeadResult.UPDATED):
                        duplicate_count += 1

                except Exception as e:
                    logger.exception(f"Error processing place {place.place_id}: {e}")
                    error_count += 1
                    run_lead = RunLead(
                        workspace_id=run.workspace_id,
                        run_id=run.id,
                        lead_id=None,
                        place_id=place.place_id,
                        provider="google_places",
                        source_key=place.place_id,
                        result=RunLeadResult.FAILED,
                        raw_result=place.raw_provider_data,
                        error_code=str(type(e).__name__),
                    )
                    self.db.add(run_lead)

            run.new_count = new_count
            run.duplicate_count = duplicate_count
            run.error_count = error_count
            self._emit_event(run.id, "prospection.dedup.completed", {
                "new_count": new_count,
                "duplicate_count": duplicate_count,
                "error_count": error_count,
            })

            # Stage 4: Complete
            if error_count == len(places):
                run.status = ProspectionRunStatus.FAILED
                run.error_summary = "All places failed to process"
            elif error_count > 0:
                run.status = ProspectionRunStatus.PARTIAL
                run.error_summary = f"{error_count} of {len(places)} places failed"
            else:
                run.status = ProspectionRunStatus.READY

            run.completed_at = datetime.now(UTC)
            self._emit_event(run.id, "prospection.run.completed", {
                "status": run.status.value,
                "new_count": new_count,
                "duplicate_count": duplicate_count,
                "error_count": error_count,
            })

        except Exception as e:
            logger.exception(f"Run {run_id} failed: {e}")
            run.status = ProspectionRunStatus.FAILED
            run.error_summary = str(e)
            run.completed_at = datetime.now(UTC)
            self._emit_event(run.id, "prospection.run.failed", {"error": str(e)})

        self.db.flush()

    async def _execute_lead_fit_discovery(self, run: ProspectionRun) -> None:
        """Run approved Maps discovery and persist its original evidence."""
        from app.modules.lead_fit.candidates import (
            normalize_place_candidate,
        )
        from app.modules.lead_fit.evidence import persist_discovery_snapshot
        from app.modules.lead_fit.service import LeadFitPolicyService

        claimed = self.db.execute(select(ProspectionRun).where(
            ProspectionRun.workspace_id == run.workspace_id,
            ProspectionRun.id == run.id,
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if claimed is None:
            return
        if claimed.completed_at and claimed.status in {
            ProspectionRunStatus.READY,
            ProspectionRunStatus.PARTIAL,
            ProspectionRunStatus.FAILED,
        }:
            return
        if claimed.started_at is not None or claimed.status is not ProspectionRunStatus.QUEUED:
            return
        run = claimed
        plan = run.search_plan_snapshot or {}
        strategy = plan.get("strategy")
        if strategy != "maps":
            run.status = ProspectionRunStatus.FAILED
            run.error_summary = "Lead Fit discovery supports Maps only"
            run.completed_at = datetime.now(UTC)
            self.db.commit()
            return
        query = plan.get("query")
        cap = plan.get("candidate_limit")
        from app.modules.lead_fit.contracts import canonical_sha256

        plan_material = {key: value for key, value in plan.items() if key != "plan_digest"}
        plan_is_valid = plan.get("plan_digest") == canonical_sha256(plan_material)
        policy = self.db.execute(select(LeadFitPolicy).where(
            LeadFitPolicy.workspace_id == run.workspace_id,
            LeadFitPolicy.id == run.fit_policy_id,
        )).scalar_one_or_none()
        current = (
            LeadFitPolicyService(self.db).current(run.workspace_id, policy.playbook_id)
            if policy is not None else None
        )
        enrichment_enabled = "enrichment" in plan
        enrichment_plan_is_valid = True
        if enrichment_enabled:
            # Imported here to keep the worker's admission check aligned with the
            # durable enrichment service without a module import cycle.
            from app.modules.prospection.enrichment import _valid_budget

            enrichment_plan_is_valid = (
                strategy == "maps" and type(cap) is int and cap <= 3 and _valid_budget(plan)
            )
        if (not plan_is_valid or not isinstance(query, str)
                or type(cap) is not int or not 1 <= cap <= 10
                or not enrichment_plan_is_valid
                or policy is None or current is None or current.id != policy.id
                or policy.policy_digest != plan.get("policy_digest")):
            run.status = ProspectionRunStatus.FAILED
            run.error_summary = "Lead Fit discovery plan is stale or invalid"
            run.completed_at = datetime.now(UTC)
            self.db.commit()
            return

        run_workspace_id = run.workspace_id
        run_id = run.id
        actor_id = run.created_by_id
        approved_policy_id = policy.id
        approved_playbook_id = policy.playbook_id
        approved_policy_digest = policy.policy_digest

        run.status = ProspectionRunStatus.SEARCHING
        run.started_at = datetime.now(UTC)
        self._emit_event(run.id, "prospection.search.started", {"strategy": strategy})
        # Copy all provider inputs before commit. SQLAlchemy expires ORM rows on
        # commit; provider calls must use this detached request snapshot so no
        # attribute access can reopen a transaction while HTTP is in flight.
        request_snapshot = SimpleNamespace(
            giro=run.giro,
            location_label=run.location_label,
            center_latitude=run.center_latitude,
            center_longitude=run.center_longitude,
            radius_km=run.radius_km,
            polygon_geometry=run.polygon_geometry,
            search_plan_snapshot=dict(plan),
        )
        self.db.commit()  # Persist the approved plan before making any provider request.
        try:
            if strategy == "maps":
                if request_snapshot.center_latitude is None and request_snapshot.center_longitude is None:
                    point = await self.resolve_location(request_snapshot)
                    request_snapshot.center_latitude = point.latitude
                    request_snapshot.center_longitude = point.longitude
                places = await self.search_places(request_snapshot)
                if request_snapshot.polygon_geometry:
                    places = [
                        place for place in places
                        if self._is_point_in_polygon(place, request_snapshot.polygon_geometry)
                    ]
                candidates = [normalize_place_candidate(place) for place in places[:cap]]

            if strategy == "maps" and (
                run.center_latitude != request_snapshot.center_latitude
                or run.center_longitude != request_snapshot.center_longitude
            ):
                run.center_latitude = request_snapshot.center_latitude
                run.center_longitude = request_snapshot.center_longitude

            current = LeadFitPolicyService(self.db).current(run.workspace_id, policy.playbook_id)
            if current is None or current.id != policy.id or current.policy_digest != plan.get("policy_digest"):
                run.status = ProspectionRunStatus.FAILED
                run.error_summary = "Approved policy changed during discovery"
                run.completed_at = datetime.now(UTC)
                self.db.commit()
                return

            run.found_count = len(candidates)
            self._emit_event(run.id, "prospection.search.completed", {"found_count": run.found_count})
            created = duplicates = errors = 0
            evaluation_lead_ids: list[UUID] = []
            for candidate in candidates:
                try:
                    if candidate.provider == "google_places":
                        place = next(
                            value for value in places if value.place_id == candidate.source_key
                        )
                        business, is_new = self._deduplicate_place(place, run.workspace_id)
                    else:
                        business = self.db.execute(select(Business).where(
                            Business.workspace_id == run.workspace_id,
                            Business.provider == "tavily",
                            Business.source_key == candidate.source_key,
                        )).scalar_one_or_none()
                        is_new = business is None
                        if business is None:
                            business = Business(
                                workspace_id=run.workspace_id,
                                place_id=None,
                                provider="tavily",
                                source_key=candidate.source_key,
                                name=candidate.name[:255],
                                website=candidate.canonical_url,
                                raw_provider_data=candidate.raw_evidence,
                            )
                            self.db.add(business)
                            self.db.flush()
                    lead, result = self._create_or_update_lead(business, run, is_new)
                    self.db.add(RunLead(
                        workspace_id=run.workspace_id, run_id=run.id, lead_id=lead.id,
                        place_id=candidate.real_place_id, provider=candidate.provider,
                        source_key=candidate.source_key, result=result,
                        raw_result=candidate.raw_evidence,
                    ))
                    persist_discovery_snapshot(
                        self.db, workspace_id=run.workspace_id, lead_id=lead.id,
                        candidate=candidate, query=query,
                        max_age_hours=FitPolicyContent.model_validate(policy.content).max_evidence_age_hours,
                        policy_digest=policy.policy_digest,
                    )
                    if lead.id not in evaluation_lead_ids:
                        evaluation_lead_ids.append(lead.id)
                    if result is RunLeadResult.NEW:
                        created += 1
                    else:
                        duplicates += 1
                except SQLAlchemyError:
                    raise
                except Exception as exc:
                    errors += 1
                    self.db.add(RunLead(
                        workspace_id=run.workspace_id, run_id=run.id, lead_id=None,
                        place_id=candidate.real_place_id, provider=candidate.provider,
                        source_key=candidate.source_key, result=RunLeadResult.FAILED,
                        raw_result=None, error_code=type(exc).__name__,
                    ))
            run.new_count = created
            run.duplicate_count = duplicates
            run.error_count = errors
            has_evaluation_work = bool(evaluation_lead_ids)
            if enrichment_enabled:
                run.status = ProspectionRunStatus.ENRICHING
                run.completed_at = None
                run.error_summary = None
                self._emit_event(run.id, "prospection.initial_enrichment.started", {
                    "lead_count": len(evaluation_lead_ids),
                })
                # Maps rows and their original evidence are durable before the
                # enrichment service reserves or dispatches any Tavily request.
                self.db.commit()
                self._run_initial_enrichment(
                    workspace_id=run_workspace_id,
                    run_id=run_id,
                    policy_id=approved_policy_id,
                    lead_ids=evaluation_lead_ids,
                )

                current = LeadFitPolicyService(self.db).current(
                    run_workspace_id, approved_playbook_id,
                )
                if (current is None or current.id != approved_policy_id
                        or current.policy_digest != approved_policy_digest):
                    run.status = ProspectionRunStatus.FAILED
                    run.error_summary = "Approved policy changed during initial enrichment"
                    run.completed_at = datetime.now(UTC)
                    self._emit_event(run.id, "prospection.run.failed", {
                        "error": "approved_policy_changed_during_enrichment",
                    })
                    self.db.commit()
                    return

            run.status = ProspectionRunStatus.PARTIAL if errors or has_evaluation_work else ProspectionRunStatus.READY
            run.completed_at = datetime.now(UTC)
            run.error_summary = (
                "Lead Fit evaluation scheduling pending" if has_evaluation_work else None
            )
            self._emit_event(run.id, "prospection.run.completed", {
                "status": run.status.value, "new_count": created,
                "duplicate_count": duplicates, "error_count": errors,
            })
            if has_evaluation_work:
                for lead_id in evaluation_lead_ids:
                    self._emit_event(run.id, "prospection.fit_evaluation.scheduling_pending", {
                        "lead_id": str(lead_id),
                    })
            # Persist discovered leads and the non-ready scheduling marker before
            # any evaluation-intent SQL can fail.
            self.db.commit()
            self._create_and_enqueue_discovery_evaluations(
                workspace_id=run_workspace_id,
                run_id=run_id,
                actor_id=actor_id,
                policy_id=approved_policy_id,
                playbook_id=approved_playbook_id,
                policy_digest=approved_policy_digest,
                lead_ids=evaluation_lead_ids,
            )
        except ProspectionEvaluationPersistenceError:
            self.db.rollback()
            raise RuntimeError("Lead Fit persistence failed") from None
        except SQLAlchemyError:
            self.db.rollback()
            raise RuntimeError("Prospection persistence failed") from None
        except Exception as exc:
            self.db.rollback()
            run.status = ProspectionRunStatus.FAILED
            run.error_summary = f"Discovery failed ({type(exc).__name__})"
            run.completed_at = datetime.now(UTC)
            self.db.commit()

    def _run_initial_enrichment(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        policy_id: UUID,
        lead_ids: list[UUID],
    ) -> None:
        """Run bounded Tavily work sequentially after Maps evidence is durable."""
        from app.modules.prospect_research.models import ProspectResearchStatus
        from app.modules.prospection.enrichment import InitialEnrichmentService

        factory = self.enrichment_service_factory or InitialEnrichmentService
        service = factory(self.db)
        for lead_id in lead_ids:
            try:
                research = service.enrich(workspace_id, lead_id, run_id, policy_id)
            except SQLAlchemyError:
                self.db.rollback()
                raise
            except Exception:
                # Provider/detail text is intentionally excluded from durable
                # events. The enrichment helper owns its durable reservation.
                self.db.rollback()
                self._emit_event(run_id, "prospection.initial_enrichment.failed", {
                    "lead_id": str(lead_id),
                    "status": "failed",
                    "error_code": "initial_enrichment_failed",
                })
                self.db.commit()
                continue

            status = getattr(research, "status", None)
            status_value = getattr(status, "value", status)
            if status_value == ProspectResearchStatus.COMPLETED.value:
                event_type = "prospection.initial_enrichment.completed"
                error_code = None
                safe_status = "completed"
            else:
                event_type = "prospection.initial_enrichment.failed"
                raw_error_code = getattr(research, "error", None)
                error_code = (
                    raw_error_code if isinstance(raw_error_code, str)
                    and raw_error_code.replace("_", "").isalnum()
                    and len(raw_error_code) <= 64
                    else "initial_enrichment_incomplete"
                )
                safe_status = "failed"
            self._emit_event(run_id, event_type, {
                "lead_id": str(lead_id),
                "status": safe_status,
                "error_code": error_code,
            })
            self.db.commit()

    def _create_and_enqueue_discovery_evaluations(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor_id: UUID,
        policy_id: UUID,
        playbook_id: UUID,
        policy_digest: str,
        lead_ids: list[UUID],
    ) -> None:
        """Persist all evaluation intents after discovery, then enqueue stable IDs."""
        if not lead_ids:
            return
        if self.evaluation_service_factory is None:
            from app.modules.lead_fit.evaluation_service import LeadFitEvaluationService

            evaluation_service = LeadFitEvaluationService(self.db)
        else:
            evaluation_service = self.evaluation_service_factory(self.db)

        pending_ids: list[tuple[UUID, UUID]] = []
        unavailable: list[tuple[UUID, str]] = []
        for lead_id in lead_ids:
            try:
                context = evaluation_service.context(
                    workspace_id=workspace_id,
                    lead_id=lead_id,
                    playbook_id=playbook_id,
                )
            except SQLAlchemyError:
                self.db.rollback()
                raise ProspectionEvaluationPersistenceError from None
            except Exception as exc:
                error_code = "stale_context" if "stale" in str(exc).lower() else "context_unavailable"
                unavailable.append((lead_id, error_code))
                logger.warning(
                    "Lead Fit evaluation context unavailable after discovery (%s)",
                    type(exc).__name__,
                )
                continue
            try:
                if context["policy_id"] != policy_id or context["policy_digest"] != policy_digest:
                    raise ValueError("approved evaluation policy changed after discovery")
                evaluation = evaluation_service.request(
                    workspace_id=workspace_id,
                    lead_id=lead_id,
                    user_id=actor_id,
                    policy_id=policy_id,
                    expected_policy_digest=policy_digest,
                    expected_source_digest=context["expected_source_digest"],
                    idempotency_key=f"discovery:{run_id}:{lead_id}:{policy_digest[:12]}",
                )
                if evaluation.status is LeadFitEvaluationStatus.PENDING:
                    pending_ids.append((evaluation.id, lead_id))
            except SQLAlchemyError:
                self.db.rollback()
                raise ProspectionEvaluationPersistenceError from None
            except Exception as exc:
                error_code = "stale_context" if "stale" in str(exc).lower() else "request_unavailable"
                unavailable.append((lead_id, error_code))
                logger.warning(
                    "Lead Fit evaluation intent unavailable after discovery (%s)",
                    type(exc).__name__,
                )
        # All intents are visible before any worker can observe an enqueued job.
        self.db.commit()

        from app.modules.lead_fit.search_flow import enqueue_lead_fit_evaluation

        for evaluation_id, lead_id in pending_ids:
            try:
                enqueue_lead_fit_evaluation(str(evaluation_id), queue=self.fit_queue)
            except Exception:
                try:
                    evaluation_service.mark_enqueue_failed(evaluation_id)
                except SQLAlchemyError:
                    self.db.rollback()
                    raise ProspectionEvaluationPersistenceError from None
                except Exception:
                    self.db.rollback()
                    raise ProspectionEvaluationPersistenceError from None
                unavailable.append((lead_id, "enqueue_failed"))

        try:
            for lead_id, error_code in unavailable:
                self._emit_event(run_id, "prospection.fit_evaluation.unavailable", {
                    "lead_id": str(lead_id),
                    "error_code": error_code,
                })
            run = self.db.execute(select(ProspectionRun).where(
                ProspectionRun.workspace_id == workspace_id,
                ProspectionRun.id == run_id,
            ).with_for_update()).scalar_one_or_none()
            if run is not None and run.status is not ProspectionRunStatus.FAILED:
                if unavailable:
                    run.status = ProspectionRunStatus.PARTIAL
                    run.error_summary = "Lead Fit evaluations unavailable for one or more discovered leads"
                elif run.error_count:
                    run.status = ProspectionRunStatus.PARTIAL
                    run.error_summary = "One or more discovered candidates could not be processed"
                else:
                    run.status = ProspectionRunStatus.READY
                    run.error_summary = None
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            raise ProspectionEvaluationPersistenceError from None
