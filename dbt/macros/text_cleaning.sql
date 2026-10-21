{#
  html_to_text: job descriptions arrive in three HTML styles (Phase 4 profiling):
    Greenhouse  escaped HTML:   &lt;p&gt;Python&lt;/p&gt;
    Reed        HTML + numeric:  <li>C&#43;&#43;</li>
    Lever       plain text + HTML "lists"
  Order matters: decode &lt; &gt; first (so escaped tags become real tags), strip tags,
  then decode the remaining entities. Unknown numeric entities become a space:
  good enough for keyword matching, not for display.
#}
{% macro html_to_text(expr) -%}
trim(regexp_replace(
  replace(replace(replace(replace(replace(replace(replace(replace(replace(
    regexp_replace(
      replace(replace(replace({{ expr }}, '&lt;', '<'), '&gt;', '>'), '&quot;', '"'),
      '<[^>]*>', ' ', 'g'),
    '&amp;', '&'), '&#43;', '+'), '&#39;', ''''), '&#8217;', ''''), '&nbsp;', ' '),
    '&#35;', '#'), '&#38;', '&'), '&#47;', '/'), '&amp;', '&'),
  '(&#?[a-z0-9]+;|\s+)', ' ', 'gi'))
{%- endmacro %}


{#  Company names: "Monzo Bank Ltd" and "monzo bank limited" are the same company. #}
{% macro normalize_company(expr) -%}
nullif(trim(regexp_replace(lower(trim({{ expr }})),
  '[,.]?\s+(ltd|limited|plc|llp|inc|uk|group)\.?$', '', 'g')), '')
{%- endmacro %}


{#  UK detection for company-board locations (Reed is UK-only).
    Matches any UK place in multi-location strings like "Barcelona; Cardiff, London or Remote (UK)". #}
{% macro uk_location_regex() -%}
'(united kingdom|\muk\M|england|scotland|wales|northern ireland|london|manchester|edinburgh|glasgow|bristol|leeds|cardiff|birmingham|cambridge|oxford|belfast|newcastle|nottingham|sheffield|liverpool|reading|brighton)'
{%- endmacro %}


{#  Scope used by every mart: a UK posting in a tracked data role family, and a real job
    (not a paid training-course advert). #}
{% macro in_market_scope(alias='') -%}
{%- set p = alias ~ '.' if alias else '' -%}
({{ p }}is_uk and {{ p }}role_family is not null and not {{ p }}is_training_course)
{%- endmacro %}
