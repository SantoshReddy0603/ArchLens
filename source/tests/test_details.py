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


def _desc(pid, text):
    s = variant(pid)
    s["project"]["description"] = text
    return recommend(s)


@pytest.mark.parametrize("pid", IDS)
def test_core_components_carry_confidence_and_sources(outputs, pid):
    st = outputs[pid]["stack"]
    for layer in ("frontend", "backend", "database", "hosting"):
        c = st[layer]
        if c.get("kb_id") is None:
            continue
        assert c["confidence"] in ("high", "medium", "low")
        assert c["margin_points"] is None or c["margin_points"] > -1
        assert c["sources"] and all(x["url"].startswith("https://") for x in c["sources"])


@pytest.mark.parametrize("pid", IDS)
def test_cost_breakdown_matches_band(outputs, pid):
    cost = outputs[pid]["estimated_monthly_cost_usd"]
    if "breakdown" not in cost:
        return
    lo = sum(b["min"] for b in cost["breakdown"])
    hi = sum(b["max"] for b in cost["breakdown"])
    assert all(b["min"] <= b["max"] for b in cost["breakdown"])
    assert abs(lo - cost["min"]) <= max(5, 0.02 * cost["min"]) and abs(hi - cost["max"]) <= max(5, 0.02 * cost["max"])


def test_database_offering_follows_hosting_provider(outputs):
    for pid in IDS:
        db = outputs[pid]["stack"]["database"]
        if db.get("offering"):
            assert db["choice"].startswith(db["offering"]) and db["engine"] in db["justification"]


def test_offering_changes_with_cloud():
    out = recommend(variant("P3"))
    db = out["stack"]["database"]
    if db.get("engine") == "PostgreSQL" and "Cloud SQL" in db["choice"]:
        aws = recommend(variant("P3", infrastructure={**variant("P3")["infrastructure"], "cloud_provider": "aws"}))
        if aws["stack"]["database"].get("engine") == "PostgreSQL" and aws["stack"]["hosting"]["choice"].startswith(("AWS", "Amazon")):
            assert "Amazon RDS" in aws["stack"]["database"]["choice"]


def test_geo_layer_follows_location_wording(outputs):
    with_gps = [x["layer"] for x in outputs["P6"]["stack"]["supporting"]]
    without = [x["layer"] for x in _desc("P6", "NGO field agents fill crop surveys with photos in villages with intermittent 2G/3G. Data syncs when signal is available.")["stack"]["supporting"]]
    if outputs["P6"]["profile"].get("hints", {}).get("location") or "geo" in with_gps:
        assert "geo" in with_gps
    assert "geo" not in without


def test_ledger_note_needs_ledger_wording_and_payments(outputs):
    assert "LEDGER_INTEGRITY" in [w["code"] for w in outputs["P4"]["warnings"]]
    plain = _desc("P4", "Small personal loans with UPI disbursal and EMI collection. KYC document upload, credit scoring model.")
    assert "LEDGER_INTEGRITY" not in [w["code"] for w in plain["warnings"]]
    for pid in ("P1", "P2", "P3", "P5", "P6"):
        assert "LEDGER_INTEGRITY" not in [w["code"] for w in outputs[pid]["warnings"]]


def test_approval_wording_adds_notification_email():
    base = variant("P2")
    if base["requirements"].get("auth") in (None, "none"):
        return
    out = _desc("P2", base["project"]["description"] + " Managers approve leave requests and expense claims.")
    assert "email" in [x["layer"] for x in out["stack"]["supporting"]]


def test_round_three_fields_are_deterministic():
    for pid in ("P3", "P4", "P6"):
        assert json.dumps(recommend(variant(pid)), sort_keys=True) == json.dumps(recommend(variant(pid)), sort_keys=True)
