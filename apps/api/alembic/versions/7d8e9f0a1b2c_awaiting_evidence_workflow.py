"""awaiting-evidence workflow and evidence-domain isolation

Revision ID: 7d8e9f0a1b2c
Revises: c4f18a2d71b0
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d8e9f0a1b2c"
down_revision: str | None = "c4f18a2d71b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_ACTIVE = "incident_id IS NOT NULL AND status IN ('running','awaiting_approval','paused')"
NEW_ACTIVE = (
    "incident_id IS NOT NULL AND status IN "
    "('running','awaiting_evidence','awaiting_approval','paused')"
)


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(
            sa.Column(
                "evidence_domain",
                sa.String(length=60),
                nullable=False,
                server_default="synthetic_facility",
            )
        )
        batch.create_index("ix_agent_runs_evidence_domain", ["evidence_domain"])

    with op.batch_alter_table("manual_chunks") as batch:
        batch.add_column(
            sa.Column(
                "evidence_domain",
                sa.String(length=60),
                nullable=False,
                server_default="synthetic_facility",
            )
        )
        batch.create_index("ix_manual_chunks_evidence_domain", ["evidence_domain"])

    with op.batch_alter_table("inspections") as batch:
        batch.add_column(
            sa.Column(
                "evidence_domain",
                sa.String(length=60),
                nullable=False,
                server_default="synthetic_facility",
            )
        )
        batch.create_index("ix_inspections_evidence_domain", ["evidence_domain"])

    with op.batch_alter_table("work_orders") as batch:
        batch.add_column(
            sa.Column(
                "evidence_domain",
                sa.String(length=60),
                nullable=False,
                server_default="synthetic_facility",
            )
        )
        batch.create_index("ix_work_orders_evidence_domain", ["evidence_domain"])

    with op.batch_alter_table("case_memory") as batch:
        batch.add_column(
            sa.Column(
                "evidence_domain",
                sa.String(length=60),
                nullable=False,
                server_default="synthetic_facility",
            )
        )
        batch.create_index("ix_case_memory_evidence_domain", ["evidence_domain"])

    op.drop_index("uq_active_run_per_incident", table_name="agent_runs")
    op.create_index(
        "uq_active_run_per_incident",
        "agent_runs",
        ["incident_id"],
        unique=True,
        sqlite_where=sa.text(NEW_ACTIVE),
        postgresql_where=sa.text(NEW_ACTIVE),
    )

    op.create_table(
        "diagnostic_evidence_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("test_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column(
            "requested_by",
            sa.String(length=80),
            nullable=False,
            server_default="deterministic_policy",
        ),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("hypotheses_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("result_schema_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("request_round", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.UniqueConstraint("run_id", "test_id", name="uq_evidence_request_run_test"),
    )
    op.create_index(
        "ix_diagnostic_evidence_requests_run_id", "diagnostic_evidence_requests", ["run_id"]
    )
    op.create_index(
        "ix_diagnostic_evidence_requests_asset_id", "diagnostic_evidence_requests", ["asset_id"]
    )
    op.create_index(
        "ix_evidence_requests_run_status", "diagnostic_evidence_requests", ["run_id", "status"]
    )

    op.create_table(
        "diagnostic_evidence_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("submitted_by", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("signal_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["diagnostic_evidence_requests.id"]),
        sa.UniqueConstraint("request_id", name="uq_evidence_observation_request"),
    )
    op.create_index(
        "ix_diagnostic_evidence_observations_request_id",
        "diagnostic_evidence_observations",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_diagnostic_evidence_observations_request_id",
        table_name="diagnostic_evidence_observations",
    )
    op.drop_table("diagnostic_evidence_observations")

    op.drop_index("ix_evidence_requests_run_status", table_name="diagnostic_evidence_requests")
    op.drop_index(
        "ix_diagnostic_evidence_requests_asset_id", table_name="diagnostic_evidence_requests"
    )
    op.drop_index(
        "ix_diagnostic_evidence_requests_run_id", table_name="diagnostic_evidence_requests"
    )
    op.drop_table("diagnostic_evidence_requests")

    op.drop_index("uq_active_run_per_incident", table_name="agent_runs")
    op.create_index(
        "uq_active_run_per_incident",
        "agent_runs",
        ["incident_id"],
        unique=True,
        sqlite_where=sa.text(OLD_ACTIVE),
        postgresql_where=sa.text(OLD_ACTIVE),
    )

    with op.batch_alter_table("case_memory") as batch:
        batch.drop_index("ix_case_memory_evidence_domain")
        batch.drop_column("evidence_domain")
    with op.batch_alter_table("work_orders") as batch:
        batch.drop_index("ix_work_orders_evidence_domain")
        batch.drop_column("evidence_domain")
    with op.batch_alter_table("inspections") as batch:
        batch.drop_index("ix_inspections_evidence_domain")
        batch.drop_column("evidence_domain")
    with op.batch_alter_table("manual_chunks") as batch:
        batch.drop_index("ix_manual_chunks_evidence_domain")
        batch.drop_column("evidence_domain")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_index("ix_agent_runs_evidence_domain")
        batch.drop_column("evidence_domain")
