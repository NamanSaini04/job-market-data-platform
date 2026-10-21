{#
  By default dbt names schemas "<target>_<custom>" (e.g. analytics_staging).
  We want plain names: staging, intermediate, marts, seeds.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}{{ target.schema }}{%- else -%}{{ custom_schema_name | trim }}{%- endif -%}
{%- endmacro %}
