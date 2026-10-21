"""dbt seeds generated from config must match config. Fails if someone edits
config/roles.yaml or config/ats_companies.yaml and forgets `python scripts/export_seeds.py`."""

import csv
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_seeds", ROOT / "scripts/export_seeds.py")
export_seeds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_seeds)


def test_seed_files_match_config():
    for name, build in export_seeds.EXPORTS.items():
        on_disk = (ROOT / "dbt/seeds" / name).read_text()
        assert on_disk == build(), f"dbt/seeds/{name} is stale: run python scripts/export_seeds.py"


def test_role_priority_follows_config_order():
    rows = list(csv.DictReader(open(ROOT / "dbt/seeds/role_patterns.csv")))
    first = {}
    for r in rows:
        first.setdefault(r["role_family"], int(r["priority"]))
    assert list(first.values()) == sorted(first.values())
    assert first["Analytics Engineer"] < first["Data Engineer"], "specific family must win over general"


def test_skill_patterns_are_valid_regex_and_unique():
    import re
    rows = list(csv.DictReader(open(ROOT / "dbt/seeds/skills.csv")))
    names = [r["skill"] for r in rows]
    assert len(names) == len(set(names))
    for r in rows:
        rx = re.compile(r["pattern"], re.I)
        # a pattern that can match nothing ("" or "x?") would tag EVERY posting with the skill
        assert not rx.fullmatch(""), f"{r['skill']}: pattern matches an empty string"
        assert rx.search(r["skill"].lower()) or r["skill"] in {"LLMs", "CI/CD"}, \
            f"{r['skill']}: pattern does not even match the skill's own name"
