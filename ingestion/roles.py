"""
Role classification by job TITLE. The one Python implementation of the rule that dbt
also applies (dbt/models/intermediate/int_posting_classification.sql).

Patterns in config/roles.yaml are REGULAR EXPRESSIONS matched anywhere in the lowercase
title (Python re.search here, Postgres `~` in dbt). Most are plain phrases
("data engineer"), which behave exactly like a substring match.

Why regex (21 Oct 2026): the plain phrase "power bi" put any title that MENTIONS the tool
into BI Developer: "Finance Business Partner (Power BI dashboards)", "Systems Accountant
(Power BI)", "Business Analyst | Power BI". A substring cannot say "power bi AND developer".

Keep patterns to syntax both engines read the same way: plain text, | ( ) .* ? and [ ].
No \\b, \\d or lookarounds (Postgres spells word boundaries differently).
"""

import re
from functools import lru_cache


@lru_cache(maxsize=None)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def title_matches(title: str, patterns: list[str]) -> bool:
    """True if the title belongs to ANY tracked role family."""
    t = (title or "").lower()
    return any(_compiled(p).search(t) for p in patterns)


def classify(title: str, roles_config: dict) -> str | None:
    """First matching family in config order (specific families are listed first)."""
    t = (title or "").lower()
    for fam in roles_config["role_families"]:
        if any(_compiled(p).search(t) for p in fam["title_patterns"]):
            return fam["name"]
    return None


def all_patterns(roles_config: dict) -> list[str]:
    return [p for fam in roles_config["role_families"] for p in fam["title_patterns"]]
