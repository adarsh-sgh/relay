"""SLO metrics, exported through Temporal's Prometheus endpoint.

Two custom series back the SLOs in docs/handoff.md:

  relay_runs{template,status}            counter, one per finished run
  relay_step_latency{activity,outcome}    histogram, seconds per activity

They ride on the SDK's own metrics (workflow/activity task latency,
failures, poll counts) so one scrape covers the whole worker.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Iterator

from temporalio import activity
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig

RUNS = "relay_runs"
STEP_LATENCY = "relay_step_latency"

DEFAULT_METRICS_ADDR = "127.0.0.1:9464"


def prometheus_runtime(bind_address: str = DEFAULT_METRICS_ADDR) -> Runtime:
    """Runtime whose /metrics endpoint serves SDK + relay series.

    One per process: pass it to Client.connect(runtime=...) and every
    worker built on that client reports into it.
    """
    return Runtime(
        telemetry=TelemetryConfig(
            metrics=PrometheusConfig(bind_address=bind_address, durations_as_seconds=True)
        )
    )


@contextmanager
def step_timer(name: str) -> Iterator[None]:
    """Record wall time of one activity attempt into relay_step_latency."""
    start = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        activity.metric_meter().create_histogram_timedelta(
            STEP_LATENCY, "wall time of one activity attempt"
        ).record(
            timedelta(seconds=time.perf_counter() - start),
            {"activity": name, "outcome": outcome},
        )
