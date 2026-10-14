"""
The untouched copy of every API response, saved before any parsing.

Layout: data/raw/<source>/<observed_date>/<board>__<run_id>.json.gz

Why keep files when we also have the database?
  * If a parsing bug is found in week 3, we rebuild from these files.
  * If the database is down during a run, nothing is lost: load the files later
    with `python -m ingestion.reload_files`.
  * They are the exact bytes the source sent: the strongest evidence of what we saw.
"""

import gzip
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

_NAME = re.compile(r"^(?P<board>.+)__(?P<run_id>[0-9a-f-]{36})\.json\.gz$")


def raw_path(data_dir: Path, source: str, observed: date, board: str, run_id: UUID) -> Path:
    return data_dir / "raw" / source / observed.isoformat() / f"{board}__{run_id}.json.gz"


def write_raw(path: Path, body: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wb") as f:
        f.write(body)
    tmp.replace(path)   # atomic: a crash never leaves a half-written file under the real name
    return path


def read_raw(path: Path) -> bytes:
    with gzip.open(path, "rb") as f:
        return f.read()


@dataclass(frozen=True)
class RawFileInfo:
    path: Path
    source: str
    observed_date: date
    board: str
    run_id: UUID


def parse_raw_path(path: Path) -> RawFileInfo:
    m = _NAME.match(path.name)
    if not m:
        raise ValueError(f"not a raw file name: {path.name}")
    return RawFileInfo(
        path=path,
        source=path.parent.parent.name,
        observed_date=date.fromisoformat(path.parent.name),
        board=m["board"],
        run_id=UUID(m["run_id"]),
    )
