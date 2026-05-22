# Analysis Plan

## Objective

Create a defensible conversion verification package that helps a data conversion analyst answer three recurring stakeholder questions:

1. Which STTM mappings are ready for formal sign-off?
2. Which load-ready to destination checks are failing or trending toward risk?
3. Which incidents need owner action before the next verification checkpoint?

## Inputs

- Field-level STTM mapping register.
- Batch-level reconciliation results.
- SQL audit findings.
- Verification incident status.

## Method

1. Generate synthetic but workflow-shaped data across six conversion domains.
2. Score reconciliation performance using row parity, key match, value match, null drift, duplicate keys, and orphan records.
3. Penalize readiness for high criticality, complex transformations, missing sign-off, and open findings.
4. Classify each mapping as Sign-off ready, Watch, Needs mapping decision, or Needs source fix.
5. Produce queues and summaries for app rendering, stakeholder review, and interview discussion.

## Review Questions

- Are critical mappings blocked by source defects or business-rule ambiguity?
- Which domains have the highest concentration of open findings?
- Which audit checks would be run in Snowflake before sign-off?
- What next update should stakeholders receive for each active incident?
