# relay

Durable LLM-agent workflow engine. Temporal gives the pipeline durability
(retries, replay, survives worker restarts), LangGraph handles agent
orchestration, and every LLM output is validated against a Pydantic schema
with automatic self-correction. Langfuse tracing is wired in and silently
no-ops when keys are absent.

On top of the engine: reusable **workflow templates** that operators
start by name with typed parameters, an **operator console** (HTTP) with
an approval queue and a per-run **audit trail** of who decided what and
why, and **SLO metrics** (run success rate, step latency) on a Prometheus
endpoint. `docs/handoff.md` is the ownership, SLO and runbook doc for the
team that inherits it.

## Architecture

```
   operator ------>+--------------------------------------+
   (curl / UI)     |  console (:8080, FastAPI)            |
                   |  templates / runs / approvals /      |
                   |  decisions          templates/*.json |
                   +-------------------+------------------+
   gRPC client --->+--------------------------------------+
   (go-client/)    |  grpc control plane (:50051)         |
                   |  StartRun / GetStatus / Approve      |
                   +-------------------+------------------+
                                       | start / query / signal
                                       v
+---------------------------+   +---------------------------+
|  Temporal server          |<->|  relay worker  /metrics --+--> Prometheus
|  (start-dev, durable      |   |                           |
|   history + task queues)  |   |  AgentPipeline workflow   |
+---------------------------+   |   plan ─ activity, retry  |
                                |     |                     |
                                |   [await decision signal] |
                                |     |    audit trail      |
                                |   execute_step xN         |
                                |     |                     |
                                |   synthesize              |
                                +------------+--------------+
                                             |
                              activities call into
                                             |
                    +------------------------v-----------------+
                    |  LangGraph graph (also runs standalone)  |
                    |                                          |
                    |   plan --> execute --> validate --+      |
                    |    ^          (tools)             |      |
                    |    +---- low confidence ----------+      |
                    |                                          |
                    |  every LLM output passes through the     |
                    |  schema-validated self-correction loop   |
                    +------------------------------------------+
```

## Features

- **Durable execution** — each pipeline stage is a Temporal activity with a
  retry policy. Completed stages are never re-run; if the worker dies
  mid-pipeline, a fresh worker resumes from history
  (`tests/test_workflow.py::test_workflow_survives_worker_restart`).
- **LangGraph orchestration** — a plan / execute / validate graph with a
  conditional edge that routes low-confidence answers back to planning.
- **Schema-validated self-correction** — LLM output must parse into a
  Pydantic model; on failure the raw output plus the validation error are
  fed back and the model is re-prompted, up to N retries
  (`relay/correction.py`).
- **Human-in-the-loop** — the workflow pauses at `awaiting_approval` and
  resumes on an `approve`/`reject` signal, delivered via the Temporal CLI,
  `examples/approve.py`, or gRPC.
- **Langfuse tracing** — spans around every node/activity and generation
  events for every LLM call. No-op unless `LANGFUSE_PUBLIC_KEY` and
  `LANGFUSE_SECRET_KEY` are set.
- **Pluggable LLM** — a `Protocol`-typed client. `OPENAI_API_KEY` selects a
  real OpenAI-compatible client (`OPENAI_BASE_URL`/`OPENAI_MODEL` override
  the endpoint); otherwise a deterministic mock, so everything, including
  tests, runs offline.
- **gRPC control plane + Go client** — start, poll, and approve pipelines
  from any language; a minimal Go client lives in `go-client/`.
- **Workflow templates** — `templates/*.json`: a name, a task string with
  `{param}` placeholders, typed params with defaults, whether approval is
  required, an owning team and an SLO. Operators never write prompts
  (`relay/templates.py`).
- **Operator console** — FastAPI service: `GET /templates`, `POST /runs`
  (template + params + requester), `GET /runs/{id}` (status + audit),
  `GET /approvals` (runs waiting on a person, with who asked and since
  when), `POST /runs/{id}/decision` (approve/reject with actor and
  reason; 409 if the run isn't waiting). Operator mistakes are 4xx with a
  message, never a stack trace (`relay/console.py`).
- **Audit trail** — every status change and decision is appended inside
  the workflow with Temporal's deterministic clock, queryable while the
  run is live and returned with the result; survives worker restarts
  because it is part of workflow state.
- **SLO metrics** — `relay_runs{template,status}` and
  `relay_step_latency{activity,outcome}` exported with the SDK's own
  metrics on the worker's Prometheus endpoint (`RELAY_METRICS_ADDR`).
  `docs/handoff.md` defines the SLOs, the PromQL, alerts and the runbook.

## Quickstart

```sh
pip install -e ".[dev]"

# terminal 1: local Temporal dev server (no cloud account needed)
temporal server start-dev

# terminal 2: worker (uses a demo LLM unless OPENAI_API_KEY is set)
python -m relay.worker

# terminal 3: start a run; it pauses for approval
python examples/run_workflow.py "compute 6*7 and report"

# terminal 4: approve it
python examples/approve.py <workflow-id>
```

The LangGraph agent also runs standalone, no Temporal required:

```sh
python examples/run_graph.py
```

gRPC control plane and the Go client:

```sh
python -m relay.grpc_server            # alongside worker + dev server
cd go-client && go run . -task "compute 6*7 and report"
```

Operator console (alongside worker + dev server; `pip install -e ".[console]"`):

```sh
python -m relay.console                # :8080
curl localhost:8080/templates
curl -X POST localhost:8080/runs -H 'content-type: application/json' \
  -d '{"template":"arithmetic-check","params":{"expression":"6*7"},"requested_by":"sales-ops@example.com"}'
curl localhost:8080/approvals          # who is waiting, since when
curl -X POST localhost:8080/runs/<id>/decision -H 'content-type: application/json' \
  -d '{"verdict":"approve","actor":"lead@example.com","reason":"plan matches request"}'
curl localhost:8080/runs/<id>          # status + audit trail
curl localhost:9464/metrics | grep relay_
```

## Tests

```sh
pytest
```

Broad end-to-end tests with a mocked LLM: the self-correction loop, the
full graph run, and — against Temporal's time-skipping test server —
approval/rejection flows, injected activity failures retried to success,
worker-restart durability, the gRPC flow, the operator flow over the
console (start from template, queue, decide, audit), and a scrape of the
Prometheus endpoint after one completed and one failed run. First run
downloads the Temporal test server binary.

## Layout

```
relay/            package: llm, schemas, correction, tools, graph,
                  workflow, worker, grpc_server, tracing,
                  templates, console, metrics
templates/        workflow templates (json), one owner + SLO each
docs/handoff.md   ownership, SLOs, alerts, runbook
proto/            relay.proto (control plane contract)
go-client/        Go gRPC client
examples/         runnable entrypoints
tests/            end-to-end tests
```

Regenerate gRPC stubs after editing `proto/relay.proto`:

```sh
python -m grpc_tools.protoc -Iproto --python_out=relay/relaypb \
    --grpc_python_out=relay/relaypb proto/relay.proto
protoc -Iproto --go_out=go-client/relaypb --go_opt=paths=source_relative \
    --go-grpc_out=go-client/relaypb --go-grpc_opt=paths=source_relative proto/relay.proto
```

MIT licensed.
