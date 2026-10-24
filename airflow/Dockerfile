# Airflow image + two separate Python environments for the work itself.
#
# Why three environments, not one:
#   Airflow and dbt pin many of the same libraries (protobuf, jinja2, pydantic...) to
#   different versions. Airflow only ORCHESTRATES: it runs commands. The collectors and dbt
#   each get their own venv, so upgrading one can never break another.
#
# Why the pip retries/timeout:
#   The first two builds on the Mac failed while fetching packages (one reported a version
#   conflict, one "no versions found" for PyYAML). The SAME install, run with `docker run`,
#   succeeded, and the image sets no pip index or constraints. So the failures were failed
#   downloads during the build; pip now retries instead of giving up (docs/06_airflow.md).
FROM apache/airflow:3.3.2-python3.12

ENV PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10

COPY airflow/requirements-pipeline.txt /tmp/requirements-pipeline.txt

RUN python -m venv /home/airflow/.venvs/pipeline \
 && /home/airflow/.venvs/pipeline/bin/pip install --no-cache-dir -r /tmp/requirements-pipeline.txt \
 && python -m venv /home/airflow/.venvs/dbt \
 && /home/airflow/.venvs/dbt/bin/pip install --no-cache-dir dbt-core==1.12.6 dbt-postgres==1.9.1 \
 && pip install --no-cache-dir pytest==8.4.2
