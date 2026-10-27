"""Enforce tenant ownership for Setter execution references."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def _assert_setter_execution_tenant_integrity() -> None:
    violations = op.get_bind().execute(
        sa.text(
            """
            SELECT relation, violation_count
            FROM (
                SELECT 'leads' AS relation, count(*) AS violation_count
                FROM setter_executions AS execution
                JOIN leads AS related ON related.id = execution.lead_id
                WHERE execution.workspace_id <> related.workspace_id
                UNION ALL
                SELECT 'prospect_analyses', count(*)
                FROM setter_executions AS execution
                JOIN prospect_analyses AS related ON related.id = execution.analysis_id
                WHERE execution.workspace_id <> related.workspace_id
                UNION ALL
                SELECT 'prospect_research', count(*)
                FROM setter_executions AS execution
                JOIN prospect_research AS related ON related.id = execution.research_id
                WHERE execution.workspace_id <> related.workspace_id
                UNION ALL
                SELECT 'sales_playbooks', count(*)
                FROM setter_executions AS execution
                JOIN sales_playbooks AS related ON related.id = execution.playbook_id
                WHERE execution.workspace_id <> related.workspace_id
            ) AS relation_violations
            WHERE violation_count > 0
            ORDER BY relation
            """
        )
    ).all()

    if violations:
        counts = ", ".join(f"{relation}={count}" for relation, count in violations)
        raise RuntimeError(
            "Cannot apply migration 0024: setter_executions contains cross-workspace "
            f"references ({counts}). Reconcile each reference with a row in the same "
            "workspace, then rerun the migration. No data was changed."
        )


def upgrade() -> None:
    _assert_setter_execution_tenant_integrity()

    op.create_unique_constraint("uq_leads_workspace_id", "leads", ["workspace_id", "id"])
    op.create_unique_constraint(
        "uq_prospect_analyses_workspace_id", "prospect_analyses", ["workspace_id", "id"]
    )
    op.create_unique_constraint(
        "uq_prospect_research_workspace_id", "prospect_research", ["workspace_id", "id"]
    )
    op.create_unique_constraint(
        "uq_sales_playbooks_workspace_id", "sales_playbooks", ["workspace_id", "id"]
    )

    op.drop_constraint("fk_setter_executions_lead_id_leads", "setter_executions", type_="foreignkey")
    op.drop_constraint(
        "fk_setter_executions_analysis_id_prospect_analyses", "setter_executions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_setter_executions_research_id_prospect_research", "setter_executions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_setter_executions_playbook_id_sales_playbooks", "setter_executions", type_="foreignkey"
    )

    op.create_foreign_key(
        "fk_setter_executions_workspace_id_leads",
        "setter_executions",
        "leads",
        ["workspace_id", "lead_id"],
        ["workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_setter_executions_workspace_id_prospect_analyses",
        "setter_executions",
        "prospect_analyses",
        ["workspace_id", "analysis_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_setter_executions_workspace_id_prospect_research",
        "setter_executions",
        "prospect_research",
        ["workspace_id", "research_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_setter_executions_workspace_id_sales_playbooks",
        "setter_executions",
        "sales_playbooks",
        ["workspace_id", "playbook_id"],
        ["workspace_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_setter_executions_workspace_id_sales_playbooks", "setter_executions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_setter_executions_workspace_id_prospect_research", "setter_executions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_setter_executions_workspace_id_prospect_analyses", "setter_executions", type_="foreignkey"
    )
    op.drop_constraint("fk_setter_executions_workspace_id_leads", "setter_executions", type_="foreignkey")

    op.create_foreign_key(
        "fk_setter_executions_playbook_id_sales_playbooks",
        "setter_executions",
        "sales_playbooks",
        ["playbook_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_setter_executions_research_id_prospect_research",
        "setter_executions",
        "prospect_research",
        ["research_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_setter_executions_analysis_id_prospect_analyses",
        "setter_executions",
        "prospect_analyses",
        ["analysis_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_setter_executions_lead_id_leads",
        "setter_executions",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("uq_sales_playbooks_workspace_id", "sales_playbooks", type_="unique")
    op.drop_constraint("uq_prospect_research_workspace_id", "prospect_research", type_="unique")
    op.drop_constraint("uq_prospect_analyses_workspace_id", "prospect_analyses", type_="unique")
    op.drop_constraint("uq_leads_workspace_id", "leads", type_="unique")
