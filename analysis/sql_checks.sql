-- Snowflake-style audit patterns for a conversion verification analyst.
-- Table names mirror the synthetic CSVs in this portfolio artifact.

-- 1. Source-to-target row parity by mapping and UAT batch.
with load_ready as (
  select
    mapping_id,
    batch_id,
    count(*) as load_ready_rows
  from load_ready.conversion_extract_profile
  where batch_id = 'CONV-UAT-04'
  group by 1, 2
),
target_profile as (
  select
    mapping_id,
    batch_id,
    count(*) as target_rows
  from target.conversion_destination_profile
  where batch_id = 'CONV-UAT-04'
  group by 1, 2
)
select
  l.mapping_id,
  l.batch_id,
  l.load_ready_rows,
  coalesce(t.target_rows, 0) as target_rows,
  coalesce(t.target_rows, 0) - l.load_ready_rows as row_count_delta,
  abs(coalesce(t.target_rows, 0) - l.load_ready_rows) / nullif(l.load_ready_rows, 0) as row_delta_rate
from load_ready l
left join target_profile t
  on l.mapping_id = t.mapping_id
 and l.batch_id = t.batch_id
where abs(coalesce(t.target_rows, 0) - l.load_ready_rows) > greatest(25, l.load_ready_rows * 0.001)
order by row_delta_rate desc;

-- 2. Latest open exception per mapping for stakeholder status updates.
select
  finding_id,
  mapping_id,
  severity,
  issue_type,
  root_cause,
  business_impact,
  status,
  updated_at
from audit.data_quality_findings
where status <> 'Closed'
qualify row_number() over (
  partition by mapping_id
  order by severity_rank asc, updated_at desc
) = 1
order by severity_rank, updated_at desc;

-- 3. Critical STTM fields where null drift exceeds approved tolerance.
with critical_mappings as (
  select
    mapping_id,
    business_domain,
    source_field,
    target_field,
    approved_null_tolerance_pct
  from governance.mapping_register
  where criticality in ('Critical', 'High')
),
profiled as (
  select
    mapping_id,
    batch_id,
    100 * sum(case when target_value is null then 1 else 0 end) / nullif(count(*), 0) as target_null_pct
  from audit.field_level_reconciliation
  where batch_id = 'CONV-UAT-04'
  group by 1, 2
)
select
  m.business_domain,
  m.mapping_id,
  m.source_field,
  m.target_field,
  p.batch_id,
  round(p.target_null_pct, 2) as target_null_pct,
  m.approved_null_tolerance_pct,
  round(p.target_null_pct - m.approved_null_tolerance_pct, 2) as null_delta_pct
from critical_mappings m
join profiled p
  on m.mapping_id = p.mapping_id
where p.target_null_pct > m.approved_null_tolerance_pct
order by null_delta_pct desc;

-- 4. Duplicate business keys after transformation.
select
  mapping_id,
  transformed_business_key,
  count(*) as duplicate_rows,
  min(source_record_id) as first_source_record_id,
  max(source_record_id) as last_source_record_id
from load_ready.conversion_records
where batch_id = 'CONV-UAT-04'
group by 1, 2
having count(*) > 1
order by duplicate_rows desc;

-- 5. Readiness queue combining STTM, reconciliation, and incident state.
with open_findings as (
  select
    mapping_id,
    count(*) as open_findings,
    count_if(severity = 'High') as high_findings,
    count_if(severity = 'Medium') as medium_findings
  from audit.data_quality_findings
  where status <> 'Closed'
  group by 1
)
select
  m.mapping_id,
  m.business_domain,
  m.source_field,
  m.target_field,
  m.signoff_status,
  r.reconciliation_score,
  coalesce(f.open_findings, 0) as open_findings,
  coalesce(f.high_findings, 0) as high_findings,
  case
    when r.reconciliation_score >= 94 and m.signoff_status = 'Approved' and coalesce(f.open_findings, 0) = 0 then 'Sign-off ready'
    when coalesce(f.high_findings, 0) > 0 then 'Needs source fix'
    when m.signoff_status in ('Pending business sign-off', 'Needs data owner decision') then 'Needs mapping decision'
    else 'Watch'
  end as gate_status
from governance.mapping_register m
join audit.reconciliation_results r
  on m.mapping_id = r.mapping_id
left join open_findings f
  on m.mapping_id = f.mapping_id
order by
  case gate_status
    when 'Needs source fix' then 1
    when 'Needs mapping decision' then 2
    when 'Watch' then 3
    else 4
  end,
  r.reconciliation_score;
