"""Ingest CLI: validate the raw MetroPT-3 CSV and produce the processed
Parquet + ingest report.

    python -m pulseops_ml.ingest            # strict checksum
    python -m pulseops_ml.ingest --no-strict-checksum
"""

from __future__ import annotations

import argparse
import json
import sys

from . import dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-strict-checksum",
        action="store_true",
        help="warn instead of fail on zip checksum mismatch",
    )
    args = parser.parse_args(argv)

    csv_path = dataset.verify_raw(strict_checksum=not args.no_strict_checksum)
    print(f"[ingest] loading {csv_path} ...")
    df, report = dataset.load_and_validate(csv_path)
    parquet_path = dataset.to_parquet(df, report)
    print(json.dumps(report, indent=2))
    print(f"[ingest] wrote {parquet_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
