"""
One HTTP session for all sources, with retries built in.

Retry policy:
  * Retries only on errors that might be temporary: 429 (rate limited) and 5xx.
  * Waits longer each time (exponential backoff: ~2s, 4s, 8s).
  * Honours a Retry-After header if the server sends one.
  * Never retries 4xx like 404: a wrong slug won't fix itself, retrying just wastes calls.
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ingestion.settings import Settings


def make_session(settings: Settings) -> requests.Session:
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
        raise_on_status=False,   # we check the final status ourselves
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = settings.user_agent
    return session
