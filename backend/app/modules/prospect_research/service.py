import ipaddress
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings, get_settings
from app.integrations.tavily import TavilyProvider, TavilyProviderError
from app.modules.leads.models import Lead
from app.modules.prospect_research.models import (
    ProspectResearch,
    ProspectResearchStatus,
    ResearchEvidence,
)

MAX_EXCERPT_LENGTH = 4000


def _research_url(url: str | None) -> str | None:
    """Validate the URL sent to Tavily; never fetch or resolve the website locally.

    Public-looking DNS names are not proof of their resolved addresses. Remote
    DNS/redirect isolation remains the extraction provider's responsibility.
    """
    if not isinstance(url, str) or not url or len(url) > 2048:
        return None
    if "\\" in url or any(ord(character) <= 32 or ord(character) == 127 for character in url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            return None
        if parsed.port not in (None, 443 if parsed.scheme == "https" else 80):
            return None
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            labels = hostname.split(".")
            if (
                len(hostname) > 253 or len(labels) < 2
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1])
                or labels[-1] in {"localhost", "local", "internal", "invalid", "test", "example", "onion"}
            ):
                return None
        else:
            if not address.is_global:
                return None
            if address.version == 6:
                hostname = f"[{address.compressed}]"
        return urlunsplit((parsed.scheme, hostname, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError):
        return None


def _domain(url: str | None) -> str | None:
    normalized = _research_url(url)
    return urlsplit(normalized).hostname.removeprefix("www.") if normalized else None


def _usage_int(usage: dict, *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return int(value)
    return None


class ProspectResearchService:
    def __init__(self, db: Session, provider: TavilyProvider | None = None, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.provider = provider or TavilyProvider(self.settings)
        self._owns_provider = provider is None

    def _lead(self, lead_id: UUID, workspace_id: UUID) -> Lead | None:
        return self.db.execute(
            select(Lead).options(joinedload(Lead.business)).where(
                Lead.id == lead_id, Lead.workspace_id == workspace_id
            )
        ).scalar_one_or_none()

    def get_latest(self, lead_id: UUID, workspace_id: UUID) -> ProspectResearch | None:
        return self.db.execute(
            select(ProspectResearch)
            .where(ProspectResearch.lead_id == lead_id, ProspectResearch.workspace_id == workspace_id)
            .order_by(ProspectResearch.requested_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    def get_latest_successful(self, lead_id: UUID, workspace_id: UUID) -> ProspectResearch | None:
        return self.db.execute(
            select(ProspectResearch)
            .where(
                ProspectResearch.lead_id == lead_id,
                ProspectResearch.workspace_id == workspace_id,
                ProspectResearch.status == ProspectResearchStatus.COMPLETED,
            )
            .order_by(ProspectResearch.completed_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def list_evidence(
        self, lead_id: UUID, workspace_id: UUID, research_id: UUID | None = None
    ) -> list[ResearchEvidence]:
        if research_id is None:
            research = self.get_latest_successful(lead_id, workspace_id)
            if not research:
                return []
            research_id = research.id
        return list(
            self.db.execute(
                select(ResearchEvidence)
                .where(
                    ResearchEvidence.id.is_not(None),
                    ResearchEvidence.research_id == research_id,
                    ResearchEvidence.lead_id == lead_id,
                    ResearchEvidence.workspace_id == workspace_id,
                )
                .order_by(ResearchEvidence.retrieved_at.desc(), ResearchEvidence.created_at.desc())
            ).scalars()
        )

    def _query_context(self, lead: Lead) -> tuple[str, dict[str, str | None]]:
        business = lead.business
        parts = [business.name, business.address, lead.giro, lead.zona]
        query = " ".join(part.strip() for part in parts if part and part.strip())[:500]
        context = {
            "business_name": business.name,
            "address": business.address,
            "website": business.website,
            "place_id": business.place_id,
        }
        return query, context

    def start_research(
        self, lead_id: UUID, workspace_id: UUID, *, force_refresh: bool = False
    ) -> tuple[ProspectResearch, bool]:
        lead = self._lead(lead_id, workspace_id)
        if not lead:
            raise ValueError("Lead not found")
        latest = self.get_latest(lead_id, workspace_id)
        if latest and latest.status in {ProspectResearchStatus.PENDING, ProspectResearchStatus.RUNNING}:
            return latest, False
        if latest and latest.status == ProspectResearchStatus.COMPLETED and not force_refresh:
            fresh_until = latest.completed_at or latest.created_at
            if datetime.now(UTC) - fresh_until <= timedelta(hours=self.settings.tavily_cache_hours):
                return latest, False

        query, context = self._query_context(lead)
        research = ProspectResearch(
            workspace_id=workspace_id,
            lead_id=lead_id,
            refresh_of_id=latest.id if latest else None,
            provider="tavily",
            operation="search_extract",
            query_summary=query,
            query_context=context,
        )
        self.db.add(research)
        self.db.flush()
        return research, True

    def _official_url(self, lead: Lead, url: str) -> bool:
        official = _domain(lead.business.website)
        candidate = _domain(url)
        return bool(official and candidate and (candidate == official or candidate.endswith(f".{official}")))

    def execute_research(self, research_id: UUID) -> ProspectResearch | None:
        research = self.db.get(ProspectResearch, research_id)
        if not research:
            return None
        if research.status == ProspectResearchStatus.COMPLETED:
            return research
        lead = self._lead(research.lead_id, research.workspace_id)
        if not lead:
            research.status = ProspectResearchStatus.FAILED
            research.error = "Lead no encontrado"
            research.completed_at = datetime.now(UTC)
            return research

        research.status = ProspectResearchStatus.RUNNING
        research.started_at = datetime.now(UTC)
        research.error = None
        self.db.flush()
        try:
            search = self.provider.search(
                research.query_summary,
                max_results=self.settings.tavily_max_results,
                search_depth=self.settings.tavily_search_depth,
            )
            research.result_count = len(search.results)
            research.provider_request_id = search.request_id
            research.search_credits = _usage_int(search.usage, "credits", "search_credits")
            evidence_index = 0
            for result in search.results:
                self.db.add(
                    ResearchEvidence(
                        evidence_id=f"tavily:{research.id}:search:{evidence_index}",
                        workspace_id=research.workspace_id,
                        research_id=research.id,
                        lead_id=research.lead_id,
                        provider="tavily",
                        operation="search",
                        source_type="search_result",
                        source_url=result.url,
                        source_title=result.title,
                        excerpt=result.content[:MAX_EXCERPT_LENGTH],
                        retrieved_at=datetime.now(UTC),
                        relevance_score=result.score,
                        metadata_json={"search_depth": self.settings.tavily_search_depth},
                    )
                )
                evidence_index += 1

            selected_urls: list[str] = []
            selection_reason = "search_result"
            if self.settings.tavily_max_extract_urls:
                official = _research_url(lead.business.website)
                if official:
                    selected_urls = [official]
                    selection_reason = "business_website"
                elif search.results:
                    top = search.results[0]
                    fallback = _research_url(top.url)
                    if fallback and (top.score is None or top.score >= 0.7):
                        selected_urls = [fallback]
            try:
                extracts, extract_usage = self.provider.extract(selected_urls)
                if len(extracts) > len(selected_urls):
                    raise TavilyProviderError("Tavily devolvió extracciones duplicadas o adicionales")
                for extract in extracts:
                    if _research_url(extract.url) not in selected_urls:
                        raise TavilyProviderError("Tavily devolvió un destino de extracción no solicitado")
            except TavilyProviderError as exc:
                extracts, extract_usage = [], {}
                research.error = str(exc)
            research.extract_credits = _usage_int(extract_usage, "credits", "extract_credits")
            research.total_credits = _usage_int(
                search.usage, "total_credits", "credits"
            )
            if research.total_credits is None and research.search_credits is not None and research.extract_credits is not None:
                research.total_credits = research.search_credits + research.extract_credits
            for extract in extracts[: self.settings.tavily_max_extract_urls]:
                self.db.add(
                    ResearchEvidence(
                        evidence_id=f"tavily:{research.id}:extract:{evidence_index}",
                        workspace_id=research.workspace_id,
                        research_id=research.id,
                        lead_id=research.lead_id,
                        provider="tavily",
                        operation="extract",
                        source_type="official_website_extract" if self._official_url(lead, extract.url) else "selected_extract",
                        source_url=_research_url(extract.url),
                        source_title=extract.title,
                        excerpt=extract.content[:MAX_EXCERPT_LENGTH],
                        retrieved_at=datetime.now(UTC),
                        metadata_json={
                            "extract_depth": "basic", "format": "markdown",
                            "requested_url": _research_url(extract.url),
                            "selection_reason": selection_reason,
                        },
                    )
                )
                evidence_index += 1
            research.extracted_count = len(extracts)
            research.status = ProspectResearchStatus.COMPLETED
            research.completed_at = datetime.now(UTC)
            self.db.flush()
            return research
        except TavilyProviderError as exc:
            research.status = ProspectResearchStatus.FAILED
            research.error = str(exc)
            research.completed_at = datetime.now(UTC)
            self.db.flush()
            return research
        except Exception:
            research.status = ProspectResearchStatus.FAILED
            research.error = "Error interno de investigación"
            research.completed_at = datetime.now(UTC)
            self.db.flush()
            return research
        finally:
            if self._owns_provider:
                self.provider.close()
