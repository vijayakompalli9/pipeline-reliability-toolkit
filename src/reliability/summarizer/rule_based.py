"""Offline, deterministic incident summarizer (the default provider)."""

from __future__ import annotations

from reliability.results import CheckResult
from reliability.summarizer.context import IncidentContext


def _find(results: list[CheckResult], *checks: str) -> list[CheckResult]:
    return [r for r in results if r.check in checks]


def _label(r: CheckResult) -> str:
    return f"`{r.dataset}`" + (f".`{r.column}`" if r.column else "")


class RuleBasedSummarizer:
    """Maps failure signatures to likely causes and runbook steps. No network, no LLM."""

    name = "rule-based (offline)"

    def hypotheses(self, ctx: IncidentContext) -> tuple[list[str], list[str]]:
        """Return (likely causes, next steps), most probable first."""
        failed = ctx.blocking + ctx.warnings
        causes: list[str] = []
        steps: list[str] = []

        drift = [r for r in _find(failed, "column_type_changed", "column_removed") if r.severity.value == "critical"]
        for r in drift:
            what = f"arrived as `{r.observed}` instead of `{r.expected}`" if r.observed else "is missing"
            causes.append(f"**Upstream schema change**: {_label(r)} {what}. Typical trigger: a producer "
                          "release or a change in export settings that was not coordinated with consumers.")
        if drift:
            steps.append("Confirm with the producing team whether the schema change is intentional. If it is, "
                         "version the contract and add an explicit cast in staging; if not, request a re-extract.")

        partial = [r for r in _find(failed, "rejected_accounting") if r.observed.get("unaccounted", 0) > 0]
        partial += [r for r in _find(failed, "row_count") if r.observed.get("diff", 0) > 0 and not partial]
        dupes = _find(failed, "duplicates", "unique")
        if partial and dupes:
            causes.append("**Failed-then-retried load**: rows are both missing and duplicated in the same run, "
                          "which matches a batch that aborted mid-way and was re-run with append (not merge) "
                          "semantics: some chunks were written twice, others never.")
        for r in partial:
            obs = r.observed
            n = obs.get("unaccounted", obs.get("diff"))
            causes.append(f"**Partial load** into {_label(r)}: {n} source rows are missing with no reject record.")
            steps.append("Compare loader chunk/offset logs with the source manifest; re-run only the missing key "
                         "range using an idempotent MERGE on the business key.")
        for r in dupes:
            causes.append(f"**Non-idempotent write** on {_label(r)}: {r.message}.")
            steps.append(f"Deduplicate {_label(r)} on the business key and switch the loader to MERGE/upsert "
                         "so retries are safe.")

        for r in _find(failed, "control_total"):
            causes.append(f"**Control total break** on {_label(r)}: {r.message}. This is a consequence of row-level "
                          "defects above and must be resolved before financial figures are published.")
            steps.append("Hold downstream financial/regulatory publication until control totals reconcile.")

        for r in _find(failed, "freshness"):
            causes.append(f"**Late delivery**: {_label(r)} {r.message}. The upstream file or job missed its window.")
            steps.append("Check the upstream scheduler and file-transfer logs for the expected landing window; "
                         "notify consumers of a possible SLA miss.")

        for r in _find(failed, "referential"):
            causes.append(f"**Orphan references** on {_label(r)}: {r.message}. Usually reference data loaded "
                          "after facts, or a late dimension feed.")
            steps.append(f"Verify the load order of `{r.expected}` and re-run the referential check after it lands.")

        handled = {"column_type_changed", "column_removed", "rejected_accounting", "row_count", "duplicates",
                   "unique", "control_total", "freshness", "referential"}
        for r in failed:
            if r.error:
                causes.append(f"**Check could not run** ({r.check} on {_label(r)}); often a symptom of type drift.")
            elif r.check not in handled:
                causes.append(f"**Data content defect** ({r.check}) on {_label(r)}: {r.message}.")

        if ctx.blast_radius:
            owners = sorted({i.owner for i in ctx.blast_radius})
            steps.append(f"Notify downstream owners: {', '.join(owners)}.")
        steps.append("After the fix, re-run the reliability gate; the circuit closes automatically when all "
                     "critical checks pass.")
        return causes, list(dict.fromkeys(steps))

    def summarize(self, ctx: IncidentContext) -> str:
        causes, steps = self.hypotheses(ctx)
        out = [
            f"# Incident note: {', '.join(ctx.datasets)}",
            "",
            f"- **Circuit state:** {ctx.state.upper()}",
            f"- **Run:** `{ctx.run_id}` (as of {ctx.as_of})",
            f"- **Checks:** {len(ctx.blocking)} blocking, {len(ctx.warnings)} warnings, {ctx.passed_count} passed",
            f"- **Generated by:** {self.name} summarizer from aggregated check metadata",
            "",
            "## What failed",
            "",
        ]
        if not (ctx.blocking or ctx.warnings):
            out.append("Nothing. All checks passed.")
        for label, group in (("critical", ctx.blocking), ("warn", ctx.warnings)):
            out += [f"- [{label}] **{r.check}** on {_label(r)}: {r.message}" for r in group]
        out += ["", "## Likely cause", ""]
        out += [f"{i}. {c}" for i, c in enumerate(causes, 1)] or ["No failure signature detected."]
        out += ["", "## Blast radius", ""]
        if ctx.blast_radius:
            out += ["| Downstream dataset | Hops | Owner | Criticality |", "|---|---|---|---|"]
            out += [f"| `{i.dataset}` | {i.depth} | {i.owner} | {i.criticality} |" for i in ctx.blast_radius]
        else:
            out.append("No downstream datasets registered in lineage.")
        out += ["", "## Suggested next steps", ""]
        out += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
        return "\n".join(out) + "\n"
