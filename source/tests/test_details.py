"""Explanation depth, extra-input effects and budget invariants."""
import json

import pytest

from conftest import IDS, choices, variant
from stacksense.engine import recommend

SECTIONS = ("project.", "scale.", "team.", "budget.", "timeline.", "infrastructure.", "requirements.", "compliance", "priorities.", "extra.")


def _sig(out):
    return (json.dumps(out["stack"], sort_keys=True), out["score"]["total"], [w["code"] for w in out["warnings"]],
            json.dumps(out["estimated_monthly_cost_usd"], sort_keys=True))


EXTRA_CASES = [("learning_appetite", "low"), ("lock_in_tolerance", "low"), ("user_geography", "global"),
               ("data_sensitivity", "public"), ("budget_strictness", "flexible"), ("hiring_plan", "aggressive"),
               ("mobile_preference", "native"), ("existing_licenses", ["aws_credits"]), ("expected_rps", 3000),
               ("cloud_credits_usd", 5000)]


@pytest.fixture(scope="module")
def p2_base():
    return _sig(recommend(variant("P2")))


@pytest.mark.parametrize("key,value", EXTRA_CASES)
def test_every_extra_input_changes_the_result(p2_base, key, value):
    extra = {key: value}
    if key == "cloud_credits_usd":
        extra["existing_licenses"] = ["aws_credits"]
    assert _sig(recommend(variant("P2", extra=extra))) != p2_base


@pytest.mark.parametrize("pid,budget", [("P2", 0), ("P2", 60), ("P2", 150), ("P3", 300), ("P3", 900), ("P4", 1500), ("P6", 20)])
def test_hard_budget_is_never_exceeded_silently(pid, budget):
    s = variant(pid, budget={"monthly_infra_usd": budget})
    s["extra"] = dict(s.get("extra") or {}, budget_strictness="hard")
    out = recommend(s)
    if out["status"] == "infeasible":
        assert any(w["severity"] == "critical" and w["code"].startswith("BUDGET") for w in out["warnings"])
    else:
        assert out["estimated_monthly_cost_usd"]["min"] <= budget


def test_native_mobile_preference_is_honoured_or_explained():
    out = recommend(variant("P2", extra={"mobile_preference": "native"}))
    fe = out["stack"]["frontend"]["choice"]
    assert "Expo" not in fe or "PREFERENCE_OUTWEIGHED" in [w["code"] for w in out["warnings"]]


def test_pwa_preference_drops_app_store_clients():
    out = recommend(variant("P2", extra={"mobile_preference": "pwa"}))
    assert "Expo" not in out["stack"]["frontend"]["choice"]
    assert set(out["profile"]["client_platforms"]) == {"web"}


@pytest.mark.parametrize("pid", IDS)
def test_architecture_style_is_explained(outputs, pid):
    arch = outputs[pid]["architecture"]
    assert arch["style"] == outputs[pid]["architecture_style"]
    assert arch["justification"] and arch["driven_by"]
    assert all(f.startswith(SECTIONS) for f in arch["driven_by"])
    if arch["style"] != "static":
        assert arch["alternatives"] and all(a["why_not"] for a in arch["alternatives"])


@pytest.mark.parametrize("pid", IDS)
def test_every_real_component_has_tradeoffs_and_alternatives(outputs, pid):
    st = outputs[pid]["stack"]
    comps = [st[k] for k in ("frontend", "backend", "database", "hosting")] + st["supporting"]
    for c in comps:
        if c["choice"] != "None":
            assert c["tradeoffs"], c["choice"]
        assert c["alternatives"], c["choice"]


def test_natively_covered_search_lists_dedicated_engines(outputs):
    search = [s for s in outputs["P2"]["stack"]["supporting"] if s["layer"] == "search"][0]
    assert search["choice"] == "PostgreSQL full-text search"
    names = [a["choice"] for a in search["alternatives"]]
    assert "No other eligible option" not in names
    assert {"Typesense", "Meilisearch", "Algolia"} & set(names)


@pytest.mark.parametrize("pid", IDS)
def test_trace_lists_each_eliminated_candidate_once(outputs, pid):
    for layer, data in outputs[pid]["decision_trace"]["layers"].items():
        ids = [e["id"] for e in data.get("eliminated", [])]
        assert len(ids) == len(set(ids)), layer


def test_india_log_retention_note_follows_residency(outputs):
    codes = {pid: [w["code"] for w in outputs[pid]["warnings"]] for pid in IDS}
    assert all("CERT_IN_LOGS" in codes[p] for p in ("P2", "P4", "P6"))
    assert "CERT_IN_LOGS" not in codes["P3"]


def test_regional_block_note_needs_supabase_and_india(outputs):
    for pid in IDS:
        out = outputs[pid]
        uses = any(out["stack"][k]["choice"].startswith("Supabase") for k in ("backend", "database"))
        india = out["profile"]["residency"] == "india"
        assert ("REGIONAL_NETWORK_BLOCK" in [w["code"] for w in out["warnings"]]) == (uses and india)


def test_health_data_without_compliance_is_flagged():
    out = recommend(variant("P5", extra={"data_sensitivity": "health"}))
    assert "SENSITIVE_DATA_UNREGULATED" in [w["code"] for w in out["warnings"]]


def test_baas_mobile_app_names_its_platform_as_hosting(outputs):
    h = outputs["P6"]["stack"]["hosting"]
    if outputs["P6"]["stack"]["backend"]["choice"].startswith("Supabase"):
        assert h["choice"] == "None (runs on Supabase)" and "Supabase" in h["justification"]


def test_new_details_are_deterministic():
    a, b = recommend(variant("P2")), recommend(variant("P2"))
    assert a == b
    assert choices(a) == choices(b)
