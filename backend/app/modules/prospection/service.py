import json
import logging
import math
import re
from datetime import UTC, datetime
from uuid import UUID

from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import require_member
from app.integrations.geocoding import GeoPoint, GoogleGeocodingProvider
from app.integrations.places import GooglePlacesProvider, PlaceCandidate, PlacesQuery
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


class ProspectionService:
    def __init__(self, db: Session):
        self.db = db
        self.geocoding_provider = GoogleGeocodingProvider()
        self.places_provider = GooglePlacesProvider()

    def create_run(self, user_id: UUID, workspace_id: UUID, run_data: ProspectionRunCreate) -> ProspectionRun:
        """Create a new prospection run."""
        require_member(self.db, user_id, workspace_id)

        run = ProspectionRun(
            workspace_id=workspace_id,
            created_by_id=user_id,
            giro=run_data.giro,
            location_label=run_data.location_label,
            center_latitude=run_data.center_latitude,
            center_longitude=run_data.center_longitude,
            radius_km=run_data.radius_km,
            polygon_geometry=(
                WKTElement(self._geometry_to_wkt(run_data.polygon_geometry), srid=4326)
                if run_data.polygon_geometry
                else None
            ),
            status=ProspectionRunStatus.QUEUED,
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
        return await self.geocoding_provider.resolve(run.location_label)

    async def search_places(self, run: ProspectionRun) -> list[PlaceCandidate]:
        """Search for places using the configured provider."""
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
            giro=run.giro,
            location_label=run.location_label,
            center_latitude=run.center_latitude,
            center_longitude=run.center_longitude,
            radius_km=radius_km,
            polygon_geometry=run.polygon_geometry,
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
            existing.updated_at = datetime.now(UTC)
            return existing, False

        # Create new business
        business = Business(
            workspace_id=workspace_id,
            place_id=place.place_id,
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
        lead = Lead(
            workspace_id=run.workspace_id,
            business_id=business.id,
            source_run_id=run.id,
            setter_id=run.created_by_id,
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

        try:
            # Stage 1: Searching
            run.status = ProspectionRunStatus.SEARCHING
            run.started_at = datetime.now(UTC)
            self._emit_event(run.id, "prospection.search.started", {})
            self.db.flush()

            # Resolve location if needed
            if run.center_latitude == 0 and run.center_longitude == 0:
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
                        run_id=run.id,
                        lead_id=lead.id,
                        place_id=place.place_id,
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
                        run_id=run.id,
                        lead_id=None,
                        place_id=place.place_id,
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
