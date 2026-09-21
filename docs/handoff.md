# Handoff: owning relay in production

Written for the team that inherits relay. Everything an on-call needs
lives here or is linked from here; if you have to read the code to
operate it, that's a bug in this doc.

## Ownership

| Component | Owner | Escalation |
|---|---|---|
| Engine (`relay/workflow.py`, worker, gRPC control plane) | platform team | `#relay-platform` |
| Console (`relay/console.py`) | platform team | `#relay-platform` |
| Each template (`templates/*.json`) | the `owner` field in the file | that team's channel |

A template's owner is accountable for its SLO and is the first responder
for failed runs of that template. Adding a template means adding an owner.

## SLOs

Per template, declared in the template file and measured from the
worker's Prometheus endpoint (`RELAY_METRICS_ADDR`, default
`127.0.0.1:9464`). Window: 30 days rolling.

| SLI | Target (defaults) | PromQL |
|---|---|---|
| Run success rate | `slo.success_rate`, e.g. 0.99 | `sum(rate(relay_runs{template="T",status="completed"}[30d])) / sum(rate(relay_runs{template="T"}[30d]))` |
| Step latency p95 | `slo.p95_step_latency_ms`, e.g. 2000 | `histogram_quantile(0.95, sum by (le) (rate(relay_step_latency_bucket{activity!="",outcome="ok"}[30d]))) * 1000` |

`relay_runs` counts every finished run once, by `template` and `status`
(`completed`, `rejected`, `failed`). `rejected` is a human decision, not
an engine failure, so it is excluded from both numerator and denominator
when reviewing the SLO: use `status!="rejected"` in the denominator.

`relay_step_latency` is one sample per activity attempt; retries show up
as `outcome="error"` samples, so `error` rate by activity is the early
warning for an upstream LLM/tool outage.

Alerts (suggested thresholds):

- Burn rate: success rate below target for 1h -> page the template owner.
- `sum(rate(relay_step_latency_count{outcome="error"}[5m])) > 0.1` -> warn platform.
- Approval queue age: any run in `awaiting_approval` for more than 4h
  (`GET /approvals`, `waiting_since`) -> nudge the requester's team.

The SDK's own series (`temporal_workflow_task_execution_failed`,
`temporal_activity_execution_failed`, poll latencies) come from the same
endpoint.

## Runbook

**A run is `failed`.** `GET /runs/{id}` shows the audit trail; the last
event carries the activity error. Activities already retried 4 times with
backoff, so a `failed` run means a persistent upstream problem (LLM key,
tool outage, schema drift). Fix the cause, then start a fresh run from
the same template and params; runs are idempotent from the caller's
point of view because every step is recorded in Temporal history.

**A run is stuck in `awaiting_approval`.** Nothing is wrong with the
engine; a person hasn't decided. `GET /approvals` lists who requested
it and since when. Approve or reject via `POST /runs/{id}/decision` with
your name and a reason; both land in the audit trail.

**Worker died mid-run.** Restart it. Temporal replays history and the
run resumes from the last completed activity; see
`tests/test_workflow.py::test_workflow_survives_worker_restart`.

**Console restarted and the approval queue looks empty.** The console
does not own state. With `VisibilityRunIndex` (the default in
`python -m relay.console`) the queue is rebuilt from Temporal visibility
on the next request. If you run it with `MemoryRunIndex`, only runs
started by that process are listed; runs are still reachable by id.

**Changing a template.** Edit the JSON, keep `owner` and `slo` honest,
run `pytest tests/test_templates.py`, ship. Running workflows keep the
task string they were started with; templates are read at start time.

**Changing the workflow code.** Temporal replays open runs against the
new code. Do not reorder or remove `execute_activity` calls without
versioning (`workflow.patched`); adding audit events is safe because they
are not commands.

## Handoff checklist

- [ ] Owner named for every template in `templates/`
- [ ] Prometheus scraping the worker; the two SLO panels exist
- [ ] Alerts above wired to the owning channels
- [ ] Console reachable by the operator team and `GET /approvals` works
- [ ] This doc linked from the on-call rotation page
