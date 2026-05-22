import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "analysis" / "outputs"
RNG = random.Random(42)

DOMAINS = [
    {
        "name": "Customer Master",
        "source_system": "Legacy CRM",
        "source_table": "CRM_CUSTOMER_ACCOUNT",
        "target_object": "CUSTOMER_DIM",
        "owner": "Customer Ops",
        "tab": "Customer STTM",
        "fields": [
            ("customer_id", "customer_key", "hash normalized legacy id"),
            ("legal_name", "customer_name", "trim, title case, preserve suffix"),
            ("tax_id", "tax_identifier", "mask where privacy flag is active"),
            ("customer_status_cd", "customer_status", "decode status reference table"),
            ("primary_email", "email_address", "lowercase and validate pattern"),
            ("billing_state", "billing_state_code", "standardize to USPS two character code"),
            ("created_ts", "source_created_at", "convert local timestamp to UTC"),
            ("updated_ts", "source_updated_at", "convert local timestamp to UTC"),
        ],
    },
    {
        "name": "Vendor Master",
        "source_system": "Procurement ERP",
        "source_table": "AP_VENDOR_PROFILE",
        "target_object": "VENDOR_DIM",
        "owner": "Procurement",
        "tab": "Vendor STTM",
        "fields": [
            ("vendor_num", "vendor_key", "left pad numeric key to 10 characters"),
            ("vendor_name", "vendor_name", "trim repeated spaces"),
            ("payment_terms", "payment_terms_code", "map terms to target reference values"),
            ("active_flag", "is_active", "convert Y or N to boolean"),
            ("remit_zip", "remit_postal_code", "standardize postal code"),
            ("bank_country", "bank_country_code", "map country name to ISO code"),
            ("last_invoice_dt", "last_invoice_date", "cast to date"),
            ("tax_form_type", "tax_form_type", "default missing values to unknown"),
        ],
    },
    {
        "name": "Contracts",
        "source_system": "Contract Repository",
        "source_table": "CNTRCT_HEADER",
        "target_object": "CONTRACT_FACT",
        "owner": "Legal Ops",
        "tab": "Contract STTM",
        "fields": [
            ("contract_id", "contract_key", "hash source contract id"),
            ("customer_id", "customer_key", "lookup customer dimension key"),
            ("effective_dt", "effective_date", "cast to date"),
            ("expiration_dt", "expiration_date", "cast to date"),
            ("contract_value", "contract_value_usd", "convert currency using close rate"),
            ("renewal_notice_days", "renewal_notice_days", "coalesce null to 0"),
            ("contract_type_cd", "contract_type", "decode contract type reference table"),
            ("signed_flag", "is_signed", "convert Y or N to boolean"),
        ],
    },
    {
        "name": "Payments",
        "source_system": "Billing Platform",
        "source_table": "PAYMENT_TXN",
        "target_object": "PAYMENT_FACT",
        "owner": "Finance",
        "tab": "Payment STTM",
        "fields": [
            ("payment_id", "payment_key", "hash payment id and source system"),
            ("invoice_id", "invoice_key", "lookup target invoice key"),
            ("customer_id", "customer_key", "lookup customer dimension key"),
            ("payment_amt", "payment_amount_usd", "convert currency using transaction rate"),
            ("payment_dt", "payment_date", "cast to date"),
            ("payment_method", "payment_method", "standardize card, ACH, wire, check"),
            ("reversal_flag", "is_reversal", "convert Y or N to boolean"),
            ("batch_id", "source_batch_id", "preserve source batch identifier"),
        ],
    },
    {
        "name": "Work Orders",
        "source_system": "Field Service",
        "source_table": "WO_HEADER",
        "target_object": "WORK_ORDER_FACT",
        "owner": "Operations",
        "tab": "Work Order STTM",
        "fields": [
            ("work_order_id", "work_order_key", "hash work order id"),
            ("customer_id", "customer_key", "lookup customer dimension key"),
            ("asset_id", "asset_key", "lookup active asset key"),
            ("opened_ts", "opened_at", "convert local timestamp to UTC"),
            ("closed_ts", "closed_at", "convert local timestamp to UTC"),
            ("priority_cd", "priority", "decode priority reference table"),
            ("labor_hours", "labor_hours", "cast decimal with two places"),
            ("resolution_cd", "resolution_code", "default missing close codes to unresolved"),
        ],
    },
    {
        "name": "Asset Register",
        "source_system": "Asset ERP",
        "source_table": "ASSET_MASTER",
        "target_object": "ASSET_DIM",
        "owner": "Asset Accounting",
        "tab": "Asset STTM",
        "fields": [
            ("asset_id", "asset_key", "hash asset id"),
            ("serial_num", "serial_number", "trim and uppercase"),
            ("install_dt", "install_date", "cast to date"),
            ("retire_dt", "retirement_date", "cast to date when populated"),
            ("book_value", "book_value_usd", "convert currency using close rate"),
            ("asset_class", "asset_class", "map to target reference values"),
            ("location_id", "location_key", "lookup target location key"),
            ("depr_method", "depreciation_method", "standardize depreciation method code"),
        ],
    },
]

ISSUE_TYPES = [
    ("Row count variance", "count parity failed between load-ready file and target table"),
    ("Null drift", "target required field has higher null rate than approved mapping"),
    ("Duplicate business key", "business key repeats after transformation logic"),
    ("Reference miss", "foreign key lookup failed against target dimension"),
    ("Value variance", "numeric or date values do not match after transformation"),
    ("Mapping ambiguity", "business rule needs stakeholder decision before sign-off"),
]


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def band(score):
    if score >= 86:
        return "Sign-off ready"
    if score >= 74:
        return "Watch"
    if score >= 62:
        return "Needs mapping decision"
    return "Needs source fix"


def severity_from_score(score, criticality):
    if score < 62 or criticality == "Critical" and score < 72:
        return "High"
    if score < 78:
        return "Medium"
    return "Low"


def make_mapping_register():
    rows = []
    complexities = ["Direct", "Lookup", "Derived", "Reference decode", "Currency conversion"]
    criticalities = ["Critical", "High", "Medium"]
    for domain_idx, domain in enumerate(DOMAINS, start=1):
        for field_idx, (source_field, target_field, rule) in enumerate(domain["fields"], start=1):
            complexity = complexities[(field_idx + domain_idx) % len(complexities)]
            criticality = criticalities[0 if field_idx in (1, 2, 5) else RNG.choices([1, 2], weights=[3, 5])[0]]
            status = RNG.choices(
                ["Approved", "Pending business sign-off", "Needs data owner decision", "Blocked by source defect"],
                weights=[44, 28, 18, 10],
            )[0]
            rows.append({
                "mapping_id": f"MAP-{domain_idx:02d}-{field_idx:02d}",
                "business_domain": domain["name"],
                "source_system": domain["source_system"],
                "source_table": domain["source_table"],
                "source_field": source_field,
                "target_object": domain["target_object"],
                "target_field": target_field,
                "data_type": RNG.choice(["varchar", "number", "date", "timestamp", "boolean"]),
                "criticality": criticality,
                "mapping_complexity": complexity,
                "transformation_rule": rule,
                "lineage_path": f"{domain['source_system']} > staging.{domain['source_table'].lower()} > load_ready.{domain['target_object'].lower()} > core.{domain['target_object'].lower()}",
                "signoff_status": status,
                "owner_group": domain["owner"],
                "google_sheet_tab": domain["tab"],
            })
    return rows


def make_reconciliation(mapping_rows):
    rows = []
    for item in mapping_rows:
        base = RNG.randint(18_000, 240_000)
        risk_bias = {
            "Approved": 0.45,
            "Pending business sign-off": 1.2,
            "Needs data owner decision": 2.3,
            "Blocked by source defect": 4.0,
        }[item["signoff_status"]]
        row_delta = int(round(RNG.gauss(0, max(2, base * 0.0008 * risk_bias))))
        matched = max(86, min(100, RNG.gauss(99.2 - risk_bias, 0.7 + risk_bias / 4)))
        value_match = max(84, min(100, RNG.gauss(98.9 - risk_bias * 0.8, 0.8 + risk_bias / 5)))
        null_delta = max(0, RNG.gauss(risk_bias * 0.55, 0.45))
        duplicate_keys = max(0, int(RNG.gauss(risk_bias * 10, 8)))
        orphan_records = max(0, int(RNG.gauss(risk_bias * 18, 16)))
        amount_variance = max(0, RNG.gauss(risk_bias * 1450, 900)) if item["data_type"] == "number" else 0
        score = round(
            matched * 0.34
            + value_match * 0.28
            + max(0, 100 - abs(row_delta) / max(base, 1) * 10000) * 0.18
            + max(0, 100 - null_delta * 11) * 0.12
            + max(0, 100 - (duplicate_keys + orphan_records) / max(base, 1) * 12000) * 0.08,
            1,
        )
        if score >= 94:
            status = "Pass"
            blocker = "None"
        elif score >= 86:
            status = "Watch"
            blocker = "Monitor variance before sign-off"
        elif item["signoff_status"] == "Needs data owner decision":
            status = "Mapping review"
            blocker = "Business rule decision required"
        else:
            status = "Fail"
            blocker = RNG.choice(["Source extract defect", "Transformation logic defect", "Reference data gap"])
        rows.append({
            "batch_id": "CONV-UAT-04",
            "mapping_id": item["mapping_id"],
            "load_ready_rows": base,
            "target_rows": base + row_delta,
            "row_count_delta": row_delta,
            "matched_keys_pct": round(matched, 2),
            "value_match_pct": round(value_match, 2),
            "null_delta_pct": round(null_delta, 2),
            "duplicate_keys": duplicate_keys,
            "orphan_records": orphan_records,
            "amount_variance_usd": round(amount_variance, 2),
            "reconciliation_score": score,
            "recon_status": status,
            "blocker_reason": blocker,
            "audit_query_id": f"AUD-{item['mapping_id'].replace('MAP-', '')}",
        })
    return rows


def make_findings(mapping_rows, recon_rows):
    rows = []
    recon_by_id = {row["mapping_id"]: row for row in recon_rows}
    idx = 1
    for item in mapping_rows:
        recon = recon_by_id[item["mapping_id"]]
        triggers = []
        if abs(int(recon["row_count_delta"])) > max(25, int(recon["load_ready_rows"]) * 0.001):
            triggers.append(ISSUE_TYPES[0])
        if float(recon["null_delta_pct"]) > 1.1:
            triggers.append(ISSUE_TYPES[1])
        if int(recon["duplicate_keys"]) > 12:
            triggers.append(ISSUE_TYPES[2])
        if int(recon["orphan_records"]) > 22:
            triggers.append(ISSUE_TYPES[3])
        if float(recon["value_match_pct"]) < 97:
            triggers.append(ISSUE_TYPES[4])
        if item["signoff_status"] in ("Needs data owner decision", "Pending business sign-off"):
            triggers.append(ISSUE_TYPES[5])
        for issue_type, signal in triggers[:2]:
            score = float(recon["reconciliation_score"])
            severity = severity_from_score(score, item["criticality"])
            rows.append({
                "finding_id": f"DQ-{idx:03d}",
                "mapping_id": item["mapping_id"],
                "severity": severity,
                "issue_type": issue_type,
                "sql_check": f"{recon['audit_query_id']}_{issue_type.lower().replace(' ', '_')}",
                "observed_signal": signal,
                "root_cause": RNG.choice([
                    "Source extract filter excludes inactive records",
                    "Reference lookup uses stale code set",
                    "Transformation rule handles nulls differently than STTM",
                    "Destination load deduplicates on the wrong business key",
                    "Business rule not approved in mapping workbook",
                ]),
                "business_impact": RNG.choice([
                    "Sign-off at risk for UAT cycle",
                    "Destination report would overstate converted volume",
                    "Downstream validation file cannot be certified",
                    "Development team needs clarified transformation rule",
                    "Stakeholder review requires exception note",
                ]),
                "owner_group": item["owner_group"],
                "status": RNG.choice(["Open", "In review", "Ready for retest"]),
                "aging_days": RNG.randint(1, 14),
            })
            idx += 1
    return rows


def make_incidents(findings):
    rows = []
    incident_candidates = sorted(
        findings,
        key=lambda row: ({"High": 0, "Medium": 1, "Low": 2}[row["severity"]], -int(row["aging_days"])),
    )
    for idx, item in enumerate(incident_candidates[:22], start=1):
        opened = date(2026, 5, 8) + timedelta(days=RNG.randint(0, 12))
        incident_severity = item["severity"]
        if incident_severity == "Low" and idx <= 8:
            incident_severity = "Medium"
        rows.append({
            "incident_id": f"INC-{idx:03d}",
            "mapping_id": item["mapping_id"],
            "verification_phase": RNG.choice(["SIT exit", "UAT cycle 4", "Mock cutover", "Sign-off review"]),
            "severity": incident_severity,
            "root_cause_category": RNG.choice(["Source defect", "Mapping decision", "Snowflake transform", "Reference data", "Load sequencing"]),
            "status": RNG.choice(["Open", "Owner assigned", "Fix in progress", "Ready for retest"]),
            "opened_date": opened.isoformat(),
            "aging_days": item["aging_days"],
            "business_impact": item["business_impact"],
            "next_update": RNG.choice([
                "Confirm owner decision in STTM sheet",
                "Retest Snowflake audit after transform patch",
                "Publish exception note for stakeholder review",
                "Re-run load-ready to target reconciliation",
            ]),
            "resolution_path": RNG.choice([
                "Patch transform and rerun validation query",
                "Update STTM rule, collect sign-off, and retest",
                "Refresh reference data and re-stage impacted records",
                "Correct source extract filter and reload batch",
            ]),
        })
    return rows


def enrich(mapping_rows, recon_rows, findings):
    recon_by_id = {row["mapping_id"]: row for row in recon_rows}
    findings_by_id = defaultdict(list)
    for finding in findings:
        findings_by_id[finding["mapping_id"]].append(finding)
    enriched = []
    for item in mapping_rows:
        recon = recon_by_id[item["mapping_id"]]
        finding_rows = findings_by_id[item["mapping_id"]]
        high_count = sum(1 for finding in finding_rows if finding["severity"] == "High")
        medium_count = sum(1 for finding in finding_rows if finding["severity"] == "Medium")
        signoff_points = {
            "Approved": 18,
            "Pending business sign-off": 8,
            "Needs data owner decision": 0,
            "Blocked by source defect": -8,
        }[item["signoff_status"]]
        complexity_penalty = {
            "Direct": 0,
            "Lookup": 4,
            "Derived": 6,
            "Reference decode": 5,
            "Currency conversion": 7,
        }[item["mapping_complexity"]]
        criticality_penalty = {"Critical": 7, "High": 4, "Medium": 1}[item["criticality"]]
        readiness = round(
            float(recon["reconciliation_score"]) * 0.62
            + signoff_points
            + 18
            - high_count * 10
            - medium_count * 5
            - complexity_penalty
            - criticality_penalty,
            1,
        )
        readiness = max(0, min(100, readiness))
        enriched.append({
            **item,
            **{f"recon_{key}": value for key, value in recon.items() if key != "mapping_id"},
            "open_findings": len(finding_rows),
            "high_findings": high_count,
            "medium_findings": medium_count,
            "readiness_score": readiness,
            "gate_status": band(readiness),
            "recommended_next_step": next_step(band(readiness), item["signoff_status"], recon["recon_status"]),
        })
    return sorted(enriched, key=lambda row: (row["readiness_score"], -row["open_findings"]))


def next_step(gate_status, signoff_status, recon_status):
    if gate_status == "Sign-off ready":
        return "Package STTM row, reconciliation evidence, and owner approval for formal sign-off."
    if signoff_status == "Needs data owner decision":
        return "Run mapping workshop and record the approved business rule in the STTM workbook."
    if recon_status == "Fail":
        return "Assign source or transform owner, patch defect, and rerun Snowflake audit suite."
    if gate_status == "Needs source fix":
        return "Block conversion approval until source extract variance is resolved."
    return "Keep in watch list and retest in the next load cycle."


def rollups(enriched):
    domain_rows = []
    by_domain = defaultdict(list)
    for row in enriched:
        by_domain[row["business_domain"]].append(row)
    for domain, rows in sorted(by_domain.items()):
        domain_rows.append({
            "business_domain": domain,
            "mappings": len(rows),
            "avg_readiness": round(sum(float(row["readiness_score"]) for row in rows) / len(rows), 1),
            "ready": sum(1 for row in rows if row["gate_status"] == "Sign-off ready"),
            "blocked": sum(1 for row in rows if row["gate_status"] in ("Needs source fix", "Needs mapping decision")),
            "open_findings": sum(int(row["open_findings"]) for row in rows),
        })
    return domain_rows


def write_outputs(enriched, findings, incidents):
    priority_fields = [
        "mapping_id",
        "business_domain",
        "source_field",
        "target_field",
        "criticality",
        "mapping_complexity",
        "signoff_status",
        "recon_reconciliation_score",
        "open_findings",
        "readiness_score",
        "gate_status",
        "recommended_next_step",
    ]
    write_csv(OUT / "priority_queue.csv", enriched, priority_fields)
    signoff_queue = [row for row in enriched if row["gate_status"] != "Sign-off ready"]
    write_csv(OUT / "signoff_queue.csv", signoff_queue, priority_fields)
    recon_summary = rollups(enriched)
    write_csv(OUT / "reconciliation_summary.csv", recon_summary, [
        "business_domain",
        "mappings",
        "avg_readiness",
        "ready",
        "blocked",
        "open_findings",
    ])
    write_csv(OUT / "incident_status.csv", incidents, [
        "incident_id",
        "mapping_id",
        "verification_phase",
        "severity",
        "root_cause_category",
        "status",
        "opened_date",
        "aging_days",
        "business_impact",
        "next_update",
        "resolution_path",
    ])
    summary = {
        "mappings": len(enriched),
        "signoff_ready": sum(1 for row in enriched if row["gate_status"] == "Sign-off ready"),
        "blocked": sum(1 for row in enriched if row["gate_status"] == "Needs source fix"),
        "mapping_decisions": sum(1 for row in enriched if row["gate_status"] == "Needs mapping decision"),
        "avg_readiness": round(sum(float(row["readiness_score"]) for row in enriched) / len(enriched), 1),
        "high_findings": sum(int(row["high_findings"]) for row in enriched),
        "open_incidents": sum(1 for row in incidents if row["status"] != "Ready for retest"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    payload = {
        "summary": summary,
        "domains": recon_summary,
        "priorityQueue": enriched,
        "findings": findings,
        "incidents": incidents,
        "sqlEvidence": [
            {
                "name": "Row count parity",
                "purpose": "Compares load-ready files to destination tables by mapping and batch.",
                "technique": "join plus aggregate variance threshold",
            },
            {
                "name": "Latest exception per mapping",
                "purpose": "Keeps the most recent defect status visible for stakeholder updates.",
                "technique": "row_number window function with qualify",
            },
            {
                "name": "Null drift by critical field",
                "purpose": "Finds required target fields with null rates above approved STTM tolerance.",
                "technique": "subquery against mapping register and grouped profile stats",
            },
        ],
    }
    (OUT / "app_payload.json").write_text(json.dumps(payload, indent=2) + "\n")


def main():
    DATA.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    mapping_rows = make_mapping_register()
    recon_rows = make_reconciliation(mapping_rows)
    findings = make_findings(mapping_rows, recon_rows)
    incidents = make_incidents(findings)
    enriched = enrich(mapping_rows, recon_rows, findings)
    write_csv(DATA / "mapping_register.csv", mapping_rows, [
        "mapping_id",
        "business_domain",
        "source_system",
        "source_table",
        "source_field",
        "target_object",
        "target_field",
        "data_type",
        "criticality",
        "mapping_complexity",
        "transformation_rule",
        "lineage_path",
        "signoff_status",
        "owner_group",
        "google_sheet_tab",
    ])
    write_csv(DATA / "reconciliation_results.csv", recon_rows, [
        "batch_id",
        "mapping_id",
        "load_ready_rows",
        "target_rows",
        "row_count_delta",
        "matched_keys_pct",
        "value_match_pct",
        "null_delta_pct",
        "duplicate_keys",
        "orphan_records",
        "amount_variance_usd",
        "reconciliation_score",
        "recon_status",
        "blocker_reason",
        "audit_query_id",
    ])
    write_csv(DATA / "data_quality_findings.csv", findings, [
        "finding_id",
        "mapping_id",
        "severity",
        "issue_type",
        "sql_check",
        "observed_signal",
        "root_cause",
        "business_impact",
        "owner_group",
        "status",
        "aging_days",
    ])
    write_csv(DATA / "incident_triage.csv", incidents, [
        "incident_id",
        "mapping_id",
        "verification_phase",
        "severity",
        "root_cause_category",
        "status",
        "opened_date",
        "aging_days",
        "business_impact",
        "next_update",
        "resolution_path",
    ])
    write_outputs(enriched, findings, incidents)
    for row in enriched[:10]:
        print(
            f"{row['mapping_id']}: readiness={row['readiness_score']}, "
            f"gate={row['gate_status']}, findings={row['open_findings']}"
        )


if __name__ == "__main__":
    main()
