"""scope replay-derived predictions and alerts

Revision ID: c4f18a2d71b0
Revises: 9f7332f169c7
Create Date: 2026-07-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f18a2d71b0"
down_revision: str | None = "9f7332f169c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shadow_alerts", sa.Column("replay_run_id", sa.Integer(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE shadow_alerts SET replay_run_id = "
            "(SELECT replay_run_id FROM model_predictions "
            " WHERE model_predictions.id = shadow_alerts.first_prediction_id)"
        )
    )

    # Batch mode keeps this migration valid for both the PostgreSQL container
    # and SQLite local development databases.
    with op.batch_alter_table("model_predictions") as batch:
        batch.drop_constraint("uq_prediction_asset_model_window", type_="unique")
        batch.create_unique_constraint(
            "uq_prediction_replay_asset_model_window",
            ["replay_run_id", "asset_id", "model_source", "window_end"],
        )
        batch.create_index("ix_predictions_replay_window", ["replay_run_id", "window_end"])

    with op.batch_alter_table("shadow_alerts") as batch:
        batch.create_foreign_key(
            "fk_shadow_alerts_replay_run_id", "replay_runs", ["replay_run_id"], ["id"]
        )
        batch.drop_constraint("uq_shadow_episode", type_="unique")
        batch.create_unique_constraint(
            "uq_shadow_replay_episode",
            ["replay_run_id", "asset_id", "policy_version", "episode_key"],
        )
        batch.create_index("ix_shadow_alerts_replay_run_id", ["replay_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("shadow_alerts") as batch:
        batch.drop_index("ix_shadow_alerts_replay_run_id")
        batch.drop_constraint("uq_shadow_replay_episode", type_="unique")
        batch.create_unique_constraint(
            "uq_shadow_episode", ["asset_id", "policy_version", "episode_key"]
        )
        batch.drop_constraint("fk_shadow_alerts_replay_run_id", type_="foreignkey")

    with op.batch_alter_table("model_predictions") as batch:
        batch.drop_index("ix_predictions_replay_window")
        batch.drop_constraint("uq_prediction_replay_asset_model_window", type_="unique")
        batch.create_unique_constraint(
            "uq_prediction_asset_model_window", ["asset_id", "model_source", "window_end"]
        )

    op.drop_column("shadow_alerts", "replay_run_id")
