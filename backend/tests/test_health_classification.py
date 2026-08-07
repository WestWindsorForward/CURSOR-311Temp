"""Health policy: optional providers degrade to warnings; critical fails loud.

Verifies the classify_health policy that drives the admin console: a downed
optional provider (AI, translation, secret store, …) is a non-blocking warning
and the app is only "critical" when a core dependency (the database) is down.
"""
import importlib

import pytest

# classify_health is pure policy, but it lives in a router module, so importing
# it drags in the whole web stack. Each of these has to be named: sqlalchemy
# alone was guarded here before, and the file went from "skipped in CI" to
# "collection error in CI" the day health.py grew its `from fastapi import` --
# a collection error makes pytest exit 2, which reads as a broken run rather
# than a missing dependency. Skip on the specific third-party imports, so a
# genuine breakage of health.py still surfaces as an error in the full image.
pytest.importorskip("fastapi.routing")           # the router decorators
pytest.importorskip("sqlalchemy")        # health.py queries the DB directly
pytest.importorskip("geoalchemy2.types")       # via app.models -> Geometry columns

health = importlib.import_module("app.api.health")


def test_all_healthy():
    out = health.classify_health({
        "database": {"status": "healthy"},
        "vertex_ai": {"status": "configured"},
        "translation_api": {"status": "disabled"},
    })
    assert out["overall_status"] == "healthy"
    assert out["warnings"] == []
    assert out["critical_failures"] == []


def test_optional_provider_down_is_a_warning_not_critical():
    out = health.classify_health({
        "database": {"status": "healthy"},
        "vertex_ai": {"status": "error", "detail": "quota exceeded"},
        "translation_api": {"status": "error", "message": "bad key"},
    })
    # App still works — degraded, never critical, for optional providers.
    assert out["overall_status"] == "degraded"
    assert out["critical_failures"] == []
    checks = {w["check"] for w in out["warnings"]}
    assert checks == {"vertex_ai", "translation_api"}
    # warning carries a human detail
    ai = next(w for w in out["warnings"] if w["check"] == "vertex_ai")
    assert ai["detail"] == "quota exceeded"


def test_database_down_is_critical_and_loud():
    out = health.classify_health({
        "database": {"status": "error", "detail": "connection refused"},
        "vertex_ai": {"status": "healthy"},
    })
    assert out["overall_status"] == "critical"
    assert len(out["critical_failures"]) == 1
    assert out["critical_failures"][0]["check"] == "database"


def test_database_down_dominates_optional_warnings():
    out = health.classify_health({
        "database": {"status": "error"},
        "vertex_ai": {"status": "error"},
    })
    # A critical failure outranks optional warnings in the overall verdict.
    assert out["overall_status"] == "critical"


def test_checks_are_tagged_with_criticality():
    out = health.classify_health({
        "database": {"status": "healthy"},
        "translation_api": {"status": "healthy"},
    })
    assert out["checks"]["database"]["critical"] is True
    assert out["checks"]["translation_api"]["critical"] is False
