"""Templates: load the repo's own templates/ dir, render with defaults, reject bad input."""

from pathlib import Path

import pytest

from relay.templates import TemplateNotFound, TemplateParamError, TemplateRegistry

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def test_registry_loads_and_renders_repo_templates():
    reg = TemplateRegistry.load(TEMPLATES_DIR)
    names = {t.name for t in reg.list()}
    assert {"arithmetic-check", "daily-digest"} <= names

    arith = reg.get("arithmetic-check")
    assert arith.render({"expression": "6*7"}) == "compute 6*7 and report"
    assert arith.require_approval and arith.owner and arith.slo.success_rate > 0

    digest = reg.get("daily-digest")
    # optional param falls back to its default
    assert digest.render({"team": "sales-ops"}) == "write a short digest of today's notes for sales-ops"
    assert digest.render({"team": "x", "length": "long"}).startswith("write a long digest")


def test_registry_rejects_bad_params_and_unknown_template():
    reg = TemplateRegistry.load(TEMPLATES_DIR)
    arith = reg.get("arithmetic-check")
    with pytest.raises(TemplateParamError, match="missing params: expression"):
        arith.render({})
    with pytest.raises(TemplateParamError, match="unknown params: typo"):
        arith.render({"expression": "1+1", "typo": "x"})
    with pytest.raises(TemplateNotFound):
        reg.get("nope")
