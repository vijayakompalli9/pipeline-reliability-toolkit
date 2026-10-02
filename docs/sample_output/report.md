# Data reliability report

- **Run:** `demo-2026-09-30`  
- **As of:** 2026-09-30T06:00:00+00:00  
- **Circuit breaker:** **OPEN** (exit code 2) - 5 blocking failure(s): column_type_changed:branch_code, row_count, control_total:amount, duplicates:payment_id, rejected_accounting  
- **Checks:** 22 total, 15 passed, 5 blocking, 2 warnings

## Schema drift

| Status | Severity | Dataset | Check | Column | Detail |
|---|---|---|---|---|---|
| FAIL | critical | payments_source | column_type_changed | branch_code | BREAKING: incompatible type integer -> string |

## Data quality

| Status | Severity | Dataset | Check | Column | Detail |
|---|---|---|---|---|---|
| FAIL | warn | payments_source | freshness | event_ts | newest record is 30.0h old (limit 24h) |
| FAIL | warn | payments_source | referential | policy_id | 6 rows reference 6 keys missing from policies.policy_id |
| PASS | critical | payments_source | not_null | payment_id | 0 null rows (0.0%) |
| PASS | critical | payments_source | not_null | policy_id | 0 null rows (0.0%) |
| PASS | critical | payments_source | not_null | payment_type | 0 null rows (0.0%) |
| PASS | critical | payments_source | not_null | amount | 0 null rows (0.0%) |
| PASS | critical | payments_source | not_null | currency | 0 null rows (0.0%) |
| PASS | critical | payments_source | not_null | status | 0 null rows (0.0%) |
| PASS | critical | payments_source | not_null | event_ts | 0 null rows (0.0%) |
| PASS | critical | payments_source | unique | payment_id | 0 keys duplicated, 0 extra rows |
| PASS | critical | payments_source | range | amount | 0 rows outside [0, 250000] |
| PASS | critical | payments_source | accepted_values | status | 0 rows with 0 unexpected distinct values |
| PASS | critical | payments_source | row_count_min |  | 5000 rows (minimum 1000) |
| PASS | warn | payments_source | regex | payment_id | 0 rows do not match pattern |
| PASS | warn | payments_source | regex | currency | 0 rows do not match pattern |
| PASS | warn | payments_source | accepted_values | payment_type | 0 rows with 0 unexpected distinct values |

## Reconciliation

| Status | Severity | Dataset | Check | Column | Detail |
|---|---|---|---|---|---|
| FAIL | critical | payments_target | row_count |  | source=5000 target=4855 diff=145 (2.9%) |
| FAIL | critical | payments_target | control_total | amount | sum(amount) source=6,428,556.83 target=6,212,911.61 diff=215,645.22 |
| FAIL | critical | payments_target | duplicates | payment_id | 25 keys loaded more than once (25 extra rows) |
| FAIL | critical | payments_target | rejected_accounting |  | 170 source keys missing from target; 20 explained by rejects; 150 unaccounted |
| PASS | warn | payments_target | null_threshold | policy_id | 0.0% null in target (limit 0.5%) |
