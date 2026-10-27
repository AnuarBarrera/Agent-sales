"""Add tenant-safe Lead Fit persistence and generic candidate identity.

This revision is intentionally not applied by the offline Lead Fit workflow.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_fit_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("commercial_digest", sa.String(64), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'approved', 'superseded')", name="ck_lead_fit_policies_status"),
        sa.CheckConstraint("version > 0", name="ck_lead_fit_policies_version_positive"),
        sa.CheckConstraint("commercial_digest ~ '^[0-9a-f]{64}$'", name="ck_lead_fit_policies_commercial_digest_sha256"),
        sa.CheckConstraint("policy_digest ~ '^[0-9a-f]{64}$'", name="ck_lead_fit_policies_policy_digest_sha256"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id", "playbook_id"], ["sales_playbooks.workspace_id", "sales_playbooks.id"], name="fk_lead_fit_policies_playbook_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_lead_fit_policies"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_lead_fit_policies_workspace_id"),
        sa.UniqueConstraint("workspace_id", "playbook_id", "version", name="uq_lead_fit_policies_workspace_playbook_version"),
    )
    op.create_index("ix_lead_fit_policies_workspace_id", "lead_fit_policies", ["workspace_id"])
    op.create_index("ix_lead_fit_policies_playbook_id", "lead_fit_policies", ["playbook_id"])
    op.create_index("ix_lead_fit_policies_actor_id", "lead_fit_policies", ["actor_id"])
    op.create_index("ix_lead_fit_policies_workspace_approved", "lead_fit_policies", ["workspace_id", "status"])

    op.create_table(
        "lead_fit_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("research_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("question_digest", sa.String(64), nullable=True),
        sa.Column("engine_version", sa.String(100), nullable=False),
        sa.Column("requested_model", sa.String(255), nullable=False),
        sa.Column("response_model", sa.String(255), nullable=True),
        sa.Column("result_digest", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("classification", sa.String(30), nullable=True),
        sa.Column("fit_score", sa.Integer(), nullable=True),
        sa.Column("coverage", sa.Float(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("audit", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_reserved_usd", sa.Float(), nullable=True),
        sa.Column("budget_consumed_usd", sa.Float(), nullable=True),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')", name="ck_lead_fit_evaluations_status"),
        sa.CheckConstraint("classification IS NULL OR status = 'completed'", name="ck_lead_fit_evaluations_classification_only_completed"),
        sa.CheckConstraint("status != 'completed' OR classification IS NOT NULL", name="ck_lead_fit_evaluations_completed_requires_classification"),
        sa.CheckConstraint("classification IS NULL OR classification IN ('qualified', 'needs_more_research', 'not_qualified')", name="ck_lead_fit_evaluations_classification_domain"),
        sa.CheckConstraint("fit_score IS NULL OR (fit_score >= 0 AND fit_score <= 100)", name="ck_lead_fit_evaluations_fit_score_range"),
        sa.CheckConstraint("coverage IS NULL OR (coverage >= 0 AND coverage <= 100)", name="ck_lead_fit_evaluations_coverage_range"),
        sa.CheckConstraint("call_count >= 0", name="ck_lead_fit_evaluations_call_count_nonnegative"),
        sa.CheckConstraint("call_count <= 2", name="ck_lead_fit_evaluations_call_count_limit"),
        sa.CheckConstraint("budget_reserved_usd IS NULL OR budget_reserved_usd >= 0", name="ck_lead_fit_evaluations_reserved_nonnegative"),
        sa.CheckConstraint("budget_consumed_usd IS NULL OR budget_consumed_usd >= 0", name="ck_lead_fit_evaluations_consumed_nonnegative"),
        sa.CheckConstraint("policy_digest ~ '^[0-9a-f]{64}$'", name="ck_lead_fit_evaluations_policy_digest_sha256"),
        sa.CheckConstraint("source_digest ~ '^[0-9a-f]{64}$'", name="ck_lead_fit_evaluations_source_digest_sha256"),
        sa.CheckConstraint("question_digest IS NULL OR question_digest ~ '^[0-9a-f]{64}$'", name="ck_lead_fit_evaluations_question_digest_sha256"),
        sa.CheckConstraint("result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'", name="ck_lead_fit_evaluations_result_digest_sha256"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id", "lead_id"], ["leads.workspace_id", "leads.id"], name="fk_lead_fit_evaluations_lead_tenant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id", "policy_id"], ["lead_fit_policies.workspace_id", "lead_fit_policies.id"], name="fk_lead_fit_evaluations_policy_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "research_id"], ["prospect_research.workspace_id", "prospect_research.id"], name="fk_lead_fit_evaluations_research_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "previous_id"], ["lead_fit_evaluations.workspace_id", "lead_fit_evaluations.id"], name="fk_lead_fit_evaluations_previous_tenant", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_lead_fit_evaluations"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_lead_fit_evaluations_workspace_id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_lead_fit_evaluations_workspace_idempotency"),
    )
    for column in ("workspace_id", "lead_id", "policy_id", "research_id", "idempotency_key", "status"):
        op.create_index(f"ix_lead_fit_evaluations_{column}", "lead_fit_evaluations", [column])
    op.create_index("ix_lead_fit_evaluations_workspace_lead_created", "lead_fit_evaluations", ["workspace_id", "lead_id", "requested_at"])

    op.create_table(
        "lead_fit_research_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=False, server_default="0") for name in (
            "max_search", "max_extract", "max_jev", "max_rounds", "consumed_search",
            "consumed_extract", "consumed_jev", "reserved_search", "reserved_extract",
            "reserved_jev", "unchanged_rounds",
        )],
        sa.Column("monetary_cap_usd", sa.Float(), nullable=True),
        sa.Column("consumed_usd", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("result_evaluation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'authorized', 'running', 'completed', 'failed', 'exhausted')", name="ck_lead_fit_research_grants_status"),
        sa.CheckConstraint("source_digest ~ '^[0-9a-f]{64}$'", name="ck_lead_fit_research_grants_source_digest_sha256"),
        sa.CheckConstraint("plan_hash ~ '^[0-9a-f]{64}$'", name="ck_lead_fit_research_grants_plan_hash_sha256"),
        sa.CheckConstraint("max_search BETWEEN 0 AND 1 AND max_extract BETWEEN 0 AND 1 AND max_jev BETWEEN 0 AND 2 AND max_rounds BETWEEN 0 AND 1", name="ck_lead_fit_research_grants_max_limits"),
        sa.CheckConstraint("consumed_search >= 0 AND consumed_extract >= 0 AND consumed_jev >= 0", name="ck_lead_fit_research_grants_consumed_nonnegative"),
        sa.CheckConstraint("reserved_search >= 0 AND reserved_extract >= 0 AND reserved_jev >= 0", name="ck_lead_fit_research_grants_reserved_nonnegative"),
        sa.CheckConstraint("reserved_search <= max_search AND reserved_extract <= max_extract AND reserved_jev <= max_jev", name="ck_lead_fit_research_grants_reserved_within_max"),
        sa.CheckConstraint("consumed_search <= reserved_search AND consumed_extract <= reserved_extract AND consumed_jev <= reserved_jev", name="ck_lead_fit_research_grants_consumed_within_reserved"),
        sa.CheckConstraint("unchanged_rounds >= 0", name="ck_lead_fit_research_grants_unchanged_rounds_nonnegative"),
        sa.CheckConstraint("monetary_cap_usd IS NULL OR monetary_cap_usd >= 0", name="ck_lead_fit_research_grants_cap_nonnegative"),
        sa.CheckConstraint("consumed_usd IS NULL OR consumed_usd >= 0", name="ck_lead_fit_research_grants_consumed_usd_nonnegative"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id", "lead_id"], ["leads.workspace_id", "leads.id"], name="fk_lead_fit_research_grants_lead_tenant", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id", "evaluation_id"], ["lead_fit_evaluations.workspace_id", "lead_fit_evaluations.id"], name="fk_lead_fit_research_grants_evaluation_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "policy_id"], ["lead_fit_policies.workspace_id", "lead_fit_policies.id"], name="fk_lead_fit_research_grants_policy_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id", "result_evaluation_id"], ["lead_fit_evaluations.workspace_id", "lead_fit_evaluations.id"], name="fk_lead_fit_research_grants_result_evaluation_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_lead_fit_research_grants"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_lead_fit_research_grants_workspace_id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_lead_fit_research_grants_workspace_idempotency"),
    )
    for column in ("workspace_id", "lead_id", "evaluation_id", "policy_id", "actor_id", "idempotency_key"):
        op.create_index(f"ix_lead_fit_research_grants_{column}", "lead_fit_research_grants", [column])
    op.create_index("ix_lead_fit_research_grants_workspace_lead", "lead_fit_research_grants", ["workspace_id", "lead_id"])

    # Keep the approval snapshot immutable while retaining its historical actor.
    op.execute("""
        CREATE FUNCTION reject_lead_fit_policy_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'lead fit policy approvals are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_lead_fit_policy_immutable
        BEFORE UPDATE OR DELETE ON lead_fit_policies
        FOR EACH ROW EXECUTE FUNCTION reject_lead_fit_policy_mutation()
    """)
    op.execute("""
        CREATE FUNCTION protect_lead_fit_grant_authorization() RETURNS trigger AS $$
        BEGIN
            IF ROW(NEW.workspace_id, NEW.lead_id, NEW.evaluation_id, NEW.policy_id,
                   NEW.actor_id, NEW.source_digest, NEW.plan_hash, NEW.plan,
                   NEW.idempotency_key, NEW.max_search, NEW.max_extract,
                   NEW.max_jev, NEW.max_rounds, NEW.monetary_cap_usd)
               IS DISTINCT FROM
               ROW(OLD.workspace_id, OLD.lead_id, OLD.evaluation_id, OLD.policy_id,
                   OLD.actor_id, OLD.source_digest, OLD.plan_hash, OLD.plan,
                   OLD.idempotency_key, OLD.max_search, OLD.max_extract,
                   OLD.max_jev, OLD.max_rounds, OLD.monetary_cap_usd) THEN
                RAISE EXCEPTION 'lead fit research authorization is immutable';
            END IF;
            IF NEW.reserved_search < OLD.reserved_search
               OR NEW.reserved_extract < OLD.reserved_extract
               OR NEW.reserved_jev < OLD.reserved_jev
               OR NEW.consumed_search < OLD.consumed_search
               OR NEW.consumed_extract < OLD.consumed_extract
               OR NEW.consumed_jev < OLD.consumed_jev THEN
                RAISE EXCEPTION 'lead fit research counters cannot be reimbursed';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_lead_fit_grant_authorization_immutable
        BEFORE UPDATE ON lead_fit_research_grants
        FOR EACH ROW EXECUTE FUNCTION protect_lead_fit_grant_authorization()
    """)

    # Add generic identity columns. Unknown legacy provenance deliberately stays NULL.
    op.add_column("businesses", sa.Column("provider", sa.String(50), nullable=True))
    op.add_column("businesses", sa.Column("source_key", sa.String(512), nullable=True))
    op.create_check_constraint("ck_businesses_identity_pair", "businesses", "(provider IS NULL AND source_key IS NULL) OR (provider IS NOT NULL AND source_key IS NOT NULL)")
    op.create_check_constraint("ck_businesses_provider_domain", "businesses", "provider IS NULL OR provider IN ('google_places', 'tavily', 'csv_import')")
    op.create_check_constraint("ck_businesses_google_place_identity_matches", "businesses", "provider IS DISTINCT FROM 'google_places' OR (place_id IS NOT NULL AND length(btrim(place_id)) > 0 AND source_key IS NOT NULL AND length(btrim(source_key)) > 0 AND place_id = source_key)")
    op.alter_column("businesses", "place_id", existing_type=sa.String(255), nullable=True)
    op.add_column("run_leads", sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_run_leads_workspace_id_workspaces", "run_leads", "workspaces", ["workspace_id"], ["id"], ondelete="CASCADE")
    op.add_column("run_leads", sa.Column("provider", sa.String(50), nullable=True))
    op.add_column("run_leads", sa.Column("source_key", sa.String(512), nullable=True))
    op.create_check_constraint("ck_run_leads_identity_pair", "run_leads", "(provider IS NULL AND source_key IS NULL) OR (provider IS NOT NULL AND source_key IS NOT NULL)")
    op.create_check_constraint("ck_run_leads_provider_domain", "run_leads", "provider IS NULL OR provider IN ('google_places', 'tavily', 'csv_import')")
    op.create_check_constraint("ck_run_leads_google_place_identity_matches", "run_leads", "provider IS DISTINCT FROM 'google_places' OR (place_id IS NOT NULL AND length(btrim(place_id)) > 0 AND source_key IS NOT NULL AND length(btrim(source_key)) > 0 AND place_id = source_key)")
    op.alter_column("run_leads", "place_id", existing_type=sa.String(255), nullable=True)
    op.execute("""
        UPDATE run_leads AS rl SET workspace_id = runs.workspace_id
        FROM prospection_runs AS runs WHERE runs.id = rl.run_id
    """)
    op.execute("""
        UPDATE businesses SET provider = 'csv_import', source_key = place_id
        WHERE place_id LIKE 'csv:%'
    """)
    op.execute("""
        UPDATE run_leads AS rl SET provider = 'csv_import', source_key = rl.place_id
        WHERE rl.place_id LIKE 'csv:%'
    """)
    op.execute("""
        UPDATE businesses AS b SET provider = 'google_places', source_key = b.place_id
        WHERE b.provider IS NULL AND b.source_key IS NULL
          AND b.place_id IS NOT NULL AND b.place_id NOT LIKE 'csv:%' AND EXISTS (
            SELECT 1 FROM run_leads AS rl
            JOIN prospection_runs AS runs ON runs.id = rl.run_id
            JOIN leads AS l ON l.id = rl.lead_id AND l.workspace_id = rl.workspace_id
            WHERE rl.workspace_id = b.workspace_id AND runs.workspace_id = b.workspace_id
              AND runs.provider = 'google_places' AND l.business_id = b.id
              AND rl.place_id = b.place_id
        )
    """)
    op.execute("""
        UPDATE run_leads AS rl SET provider = 'google_places', source_key = rl.place_id
        FROM prospection_runs AS runs, leads AS l, businesses AS b
        WHERE runs.id = rl.run_id AND runs.workspace_id = rl.workspace_id
          AND runs.provider = 'google_places' AND rl.lead_id = l.id
          AND rl.provider IS NULL AND rl.source_key IS NULL
          AND rl.place_id IS NOT NULL AND rl.place_id NOT LIKE 'csv:%'
          AND l.workspace_id = rl.workspace_id AND l.business_id = b.id
          AND b.workspace_id = rl.workspace_id AND b.provider = 'google_places'
          AND rl.place_id = b.place_id
    """)
    op.alter_column("run_leads", "workspace_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.create_unique_constraint("uq_business_workspace_provider_source", "businesses", ["workspace_id", "provider", "source_key"])
    op.create_unique_constraint("uq_businesses_workspace_id", "businesses", ["workspace_id", "id"])
    op.create_unique_constraint("uq_prospection_runs_workspace_id", "prospection_runs", ["workspace_id", "id"])
    op.create_unique_constraint("uq_run_leads_workspace_id", "run_leads", ["workspace_id", "id"])
    op.create_unique_constraint("uq_run_lead_run_provider_source", "run_leads", ["run_id", "provider", "source_key"])
    op.create_index("ix_run_leads_workspace_id", "run_leads", ["workspace_id"])

    # Replace old single-column FKs with tenant-safe constraints; nullable composite
    # references use RESTRICT so a delete cannot null the non-null workspace key.
    op.drop_constraint("fk_leads_business_id_businesses", "leads", type_="foreignkey")
    op.create_foreign_key("fk_leads_business_tenant", "leads", "businesses", ["workspace_id", "business_id"], ["workspace_id", "id"], ondelete="CASCADE")
    op.drop_constraint("fk_run_leads_run_id_prospection_runs", "run_leads", type_="foreignkey")
    op.drop_constraint("fk_run_leads_lead_id_leads", "run_leads", type_="foreignkey")
    op.create_foreign_key("fk_run_leads_run_tenant", "run_leads", "prospection_runs", ["workspace_id", "run_id"], ["workspace_id", "id"], ondelete="CASCADE")
    op.create_foreign_key("fk_run_leads_lead_tenant", "run_leads", "leads", ["workspace_id", "lead_id"], ["workspace_id", "id"], ondelete="RESTRICT")

    op.add_column("prospection_runs", sa.Column("fit_policy_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("prospection_runs", sa.Column("search_plan_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_foreign_key("fk_prospection_runs_fit_policy_tenant", "prospection_runs", "lead_fit_policies", ["workspace_id", "fit_policy_id"], ["workspace_id", "id"], ondelete="RESTRICT")
    op.alter_column("prospection_runs", "center_latitude", existing_type=sa.Float(), nullable=True)
    op.alter_column("prospection_runs", "center_longitude", existing_type=sa.Float(), nullable=True)
    op.create_check_constraint("ck_prospection_runs_center_coordinates_pair", "prospection_runs", "(center_latitude IS NULL AND center_longitude IS NULL) OR (center_latitude BETWEEN -90 AND 90 AND center_longitude BETWEEN -180 AND 180)")

    op.add_column("prospect_analyses", sa.Column("fit_evaluation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_prospect_analyses_fit_evaluation_tenant", "prospect_analyses", "lead_fit_evaluations", ["workspace_id", "fit_evaluation_id"], ["workspace_id", "id"], ondelete="RESTRICT")
    op.drop_constraint("fk_prospect_analyses_lead_id_leads", "prospect_analyses", type_="foreignkey")
    op.drop_constraint("fk_prospect_analyses_sales_playbook_id_sales_playbooks", "prospect_analyses", type_="foreignkey")
    op.drop_constraint("fk_prospect_analyses_research_id_prospect_research", "prospect_analyses", type_="foreignkey")
    op.drop_constraint("fk_prospect_analyses_previous_analysis_id_prospect_analyses", "prospect_analyses", type_="foreignkey")
    op.create_foreign_key("fk_prospect_analyses_lead_tenant", "prospect_analyses", "leads", ["workspace_id", "lead_id"], ["workspace_id", "id"], ondelete="CASCADE")
    op.create_foreign_key("fk_prospect_analyses_playbook_tenant", "prospect_analyses", "sales_playbooks", ["workspace_id", "sales_playbook_id"], ["workspace_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_prospect_analyses_research_tenant", "prospect_analyses", "prospect_research", ["workspace_id", "research_id"], ["workspace_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_prospect_analyses_previous_tenant", "prospect_analyses", "prospect_analyses", ["workspace_id", "previous_analysis_id"], ["workspace_id", "id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_lead_fit_grant_authorization_immutable ON lead_fit_research_grants")
    op.execute("DROP FUNCTION IF EXISTS protect_lead_fit_grant_authorization()")
    op.drop_constraint("ck_prospection_runs_center_coordinates_pair", "prospection_runs", type_="check")
    op.execute("DROP TRIGGER IF EXISTS trg_lead_fit_policy_immutable ON lead_fit_policies")
    op.execute("DROP FUNCTION IF EXISTS reject_lead_fit_policy_mutation()")
    op.drop_constraint("fk_prospect_analyses_previous_tenant", "prospect_analyses", type_="foreignkey")
    op.drop_constraint("fk_prospect_analyses_research_tenant", "prospect_analyses", type_="foreignkey")
    op.drop_constraint("fk_prospect_analyses_playbook_tenant", "prospect_analyses", type_="foreignkey")
    op.drop_constraint("fk_prospect_analyses_lead_tenant", "prospect_analyses", type_="foreignkey")
    op.drop_constraint("fk_prospect_analyses_fit_evaluation_tenant", "prospect_analyses", type_="foreignkey")
    op.create_foreign_key("fk_prospect_analyses_previous_analysis_id_prospect_analyses", "prospect_analyses", "prospect_analyses", ["previous_analysis_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_prospect_analyses_research_id_prospect_research", "prospect_analyses", "prospect_research", ["research_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_prospect_analyses_sales_playbook_id_sales_playbooks", "prospect_analyses", "sales_playbooks", ["sales_playbook_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_prospect_analyses_lead_id_leads", "prospect_analyses", "leads", ["lead_id"], ["id"], ondelete="CASCADE")
    op.drop_column("prospect_analyses", "fit_evaluation_id")
    op.drop_constraint("fk_prospection_runs_fit_policy_tenant", "prospection_runs", type_="foreignkey")
    op.alter_column("prospection_runs", "center_longitude", existing_type=sa.Float(), nullable=False)
    op.alter_column("prospection_runs", "center_latitude", existing_type=sa.Float(), nullable=False)
    op.drop_column("prospection_runs", "search_plan_snapshot")
    op.drop_column("prospection_runs", "fit_policy_id")
    op.drop_constraint("fk_run_leads_lead_tenant", "run_leads", type_="foreignkey")
    op.drop_constraint("fk_run_leads_run_tenant", "run_leads", type_="foreignkey")
    op.drop_constraint("fk_run_leads_workspace_id_workspaces", "run_leads", type_="foreignkey")
    op.drop_constraint("ck_run_leads_google_place_identity_matches", "run_leads", type_="check")
    op.drop_constraint("ck_run_leads_provider_domain", "run_leads", type_="check")
    op.drop_constraint("ck_run_leads_identity_pair", "run_leads", type_="check")
    op.create_foreign_key("fk_run_leads_lead_id_leads", "run_leads", "leads", ["lead_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_run_leads_run_id_prospection_runs", "run_leads", "prospection_runs", ["run_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint("fk_leads_business_tenant", "leads", type_="foreignkey")
    op.create_foreign_key("fk_leads_business_id_businesses", "leads", "businesses", ["business_id"], ["id"], ondelete="CASCADE")
    op.drop_constraint("uq_businesses_workspace_id", "businesses", type_="unique")
    op.drop_constraint("ck_businesses_google_place_identity_matches", "businesses", type_="check")
    op.drop_constraint("ck_businesses_provider_domain", "businesses", type_="check")
    op.drop_constraint("ck_businesses_identity_pair", "businesses", type_="check")
    op.drop_constraint("uq_run_leads_workspace_id", "run_leads", type_="unique")
    op.drop_constraint("uq_run_lead_run_provider_source", "run_leads", type_="unique")
    op.drop_index("ix_run_leads_workspace_id", table_name="run_leads")
    op.drop_constraint("uq_prospection_runs_workspace_id", "prospection_runs", type_="unique")
    op.drop_constraint("uq_business_workspace_provider_source", "businesses", type_="unique")
    op.drop_column("run_leads", "source_key")
    op.drop_column("run_leads", "provider")
    op.drop_column("run_leads", "workspace_id")
    op.alter_column("run_leads", "place_id", existing_type=sa.String(255), nullable=False)
    op.drop_column("businesses", "source_key")
    op.drop_column("businesses", "provider")
    op.alter_column("businesses", "place_id", existing_type=sa.String(255), nullable=False)
    for table in ("lead_fit_research_grants", "lead_fit_evaluations", "lead_fit_policies"):
        op.drop_table(table)
