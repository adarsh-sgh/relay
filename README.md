# relay

Durable LLM-agent workflow engine. Temporal gives the pipeline durability
(retries, replay, survives worker restarts), LangGraph handles agent
orchestration, and every LLM output is validated against a Pydantic schema
with automatic self-correction. Langfuse tracing is wired in and silently
no-ops when keys are absent.

## Architecture

```
                    +--------------------------------------+
   gRPC client ---->|  grpc control plane (:50051)         |
   (go-client/)     |  StartRun / GetStatus / Approve      |
                    +-------------------+------------------+
                                        | start / query / signal
                                        v
+---------------------------+   +---------------------------+
|  Temporal server          |<->|  relay worker             |
|  (start-dev, durable      |   |                           |
|   history + task queues)  |   |  AgentPipeline workflow   |
+---------------------------+   |   plan ─ activity, retry  |
                                |     |                     |
                                |   [await approval signal] |
                                |     |                     |
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
  mid-pipeline, a fresh worker resumes from history.
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

## Tests

```sh
pytest
```

Broad end-to-end tests with a mocked LLM: the self-correction loop, the
full graph run, and — against Temporal's time-skipping test server —
approval/rejection flows and the gRPC flow. First run downloads the
Temporal test server binary.

## Layout

```
relay/            package: llm, schemas, correction, tools, graph,
                  workflow, worker, grpc_server, tracing
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
