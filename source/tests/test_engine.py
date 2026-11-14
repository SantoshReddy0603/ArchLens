import copy
import json
import os
import subprocess
import sys

import pytest

from conftest import IDS, ROOT, choices, load, support_ids, variant
from stacksense import kb
from stacksense.engine import recommend
from stacksense.validate import validate
from stacksense.conflicts import detect

TOP_KEYS = ["scenario_id", "status", "summary", "architecture_style", "stack", "estimated_monthly_cost_usd",
            "warnings", "assumptions", "validation_errors", "decision_trace", "profile", "score", "ranking"]
LAYER_KEYS = {"choice", "justification", "driven_by", "alternatives", "tradeoffs"}
STATUSES = {"ok", "ok_with_warnings", "infeasible", "invalid_input"}
SEVERITIES = {"info", "warning", "critical"}


def codes(out):
    return [w["code"] for w in out["warnings"]]


def check_schema(out):
    for k in TOP_KEYS:
        assert k in out, k
    assert out["status"] in STATUSES
    json.dumps(out)
    for w in out["warnings"]:
        assert {"code", "severity", "message", "fields"} <= set(w)
        assert w["severity"] in SEVERITIES
    assert isinstance(out["assumptions"], list)
    assert isinstance(out["validation_errors"], list)
    if out["status"] in ("ok", "ok_with_warnings"):
        st = out["stack"]
        for l in ("frontend", "backend", "database", "hosting"):
            assert LAYER_KEYS <= set(st[l]), l
            assert st[l]["justification"]
            assert st[l]["driven_by"]
            for a in st[l]["alternatives"]:
                assert {"choice", "why_not"} <= set(a)
        assert isinstance(st["supporting"], list)
        c = out["estimated_monthly_cost_usd"]
        assert 0 <= c["min"] <= c["max"]
        assert c["assumptions"]
        assert out["architecture_style"] in {"monolith", "modular_monolith", "microservices", "serverless", "static",
                                              "service_oriented", "hybrid"}


# schema and per-scenario sanity

@pytest.mark.parametrize("pid", IDS)
def test_schema_and_trace(outputs, pid):
    o = outputs[pid]
    check_schema(o)
    assert o["scenario_id"] == pid
    assert o["status"] in ("ok", "ok_with_warnings")
    assert o["decision_trace"]["layers"]
    assert o["ranking"] and o["ranking"][0]["rank"] == 1
    scores = [r["score"] for r in o["ranking"]]
    assert scores[1:] == sorted(scores[1:], reverse=True)
    assert scores[0] >= scores[1] - 0.75 if len(scores) > 1 else True
    assert 0 <= o["score"]["total"] <= 100
    assert o["profile"]


def test_p1_static_site(outputs):
    o = outputs["P1"]
    c = choices(o)
    assert c["backend"] == "None" and c["database"] == "None"
    assert o["architecture_style"] == "static"
    assert o["estimated_monthly_cost_usd"]["max"] <= 60
    assert not any(k in c["frontend"] for k in ("Native", "Flutter", "Angular"))


def test_p2_mobile_seo_budget(outputs):
    o = outputs["P2"]
    c = choices(o)
    assert "React Native" in c["frontend"] or "Flutter" in c["frontend"]
    assert "Next.js" in c["frontend"] or "Nuxt" in c["frontend"] or "SvelteKit" in c["frontend"] or "Astro" in c["frontend"]
    assert c["backend"] not in ("Java + Spring Boot", "C# + ASP.NET Core")
    assert o["estimated_monthly_cost_usd"]["min"] <= 150


def test_p3_realtime(outputs):
    o = outputs["P3"]
    c = choices(o)
    assert c["backend"] in ("Go (net/http + chi)", "Node.js + NestJS", "Node.js + Express", "Elixir + Phoenix", "Node.js + Fastify")
    assert "Cloudflare Pages" not in c["hosting"] and "GitHub Pages" not in c["hosting"]
    assert c["database"] != "None"
    assert o["estimated_monthly_cost_usd"]["min"] <= 1500


def test_p4_india_scale(outputs):
    o = outputs["P4"]
    c = choices(o)
    assert "React Native" in c["frontend"] or "Flutter" in c["frontend"]
    assert c["hosting"] not in ("GitHub Pages", "Cloudflare Pages", "Vercel", "Netlify")
    s = support_ids(o)
    assert "payments" in s and "sms" in s
    assert "AIRGAPPED" not in " ".join(codes(o))


def test_p5_microsoft_estate(outputs):
    o = outputs["P5"]
    c = choices(o)
    assert c["backend"] == "C# + ASP.NET Core"
    assert "SQL Server" in c["database"]
    assert any("Entra" in v for v in support_ids(o).values())


def test_p6_offline_mobile(outputs):
    o = outputs["P6"]
    c = choices(o)
    assert "React Native" in c["frontend"] or "Flutter" in c["frontend"]
    assert not any(k in c["frontend"] for k in ("Astro", "Hugo", "Angular"))
    assert "sync" in support_ids(o) or "offline" in json.dumps(o["stack"]["frontend"]).lower()


# determinism

@pytest.mark.parametrize("pid", ["P2", "P4"])
def test_repeat_identical(scenarios, outputs, pid):
    again = recommend(copy.deepcopy(scenarios[pid]))
    assert json.dumps(again, sort_keys=True) == json.dumps(outputs[pid], sort_keys=True)


def test_input_not_mutated(scenarios):
    s = copy.deepcopy(scenarios["P3"])
    before = json.dumps(s, sort_keys=True)
    recommend(s)
    assert json.dumps(s, sort_keys=True) == before


def test_hashseed_independent(tmp_path):
    results = []
    for seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = tmp_path / ("o%s.json" % seed)
        r = subprocess.run([sys.executable, "-m", "stacksense", "run", os.path.join(ROOT, "scenarios", "P6.json"),
                            "-o", str(out), "--no-llm"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        results.append(out.read_text())
    assert results[0] == results[1]


def test_cli_run_all(tmp_path):
    from stacksense import cli
    d = tmp_path / "in"
    d.mkdir()
    for p in ("P1", "P5"):
        (d / (p + ".json")).write_text(json.dumps(load(p)))
    o = tmp_path / "out"
    cli.main(["run-all", str(d), str(o), "--no-llm"])
    assert sorted(os.listdir(o)) == ["P1_output.json", "P5_output.json"]
    check_schema(json.loads((o / "P5_output.json").read_text()))


# locality / perturbation

def test_small_perturbation_is_stable(outputs):
    base = choices(outputs["P3"])
    for users in (45000, 55000):
        s = variant("P3", scale__users_12_months=users)
        assert choices(recommend(s)) == base
    s = variant("P3", budget__monthly_infra_usd=1525)
    assert choices(recommend(s)) == base


def test_irrelevant_field_does_not_change_stack(outputs):
    s = variant("P5", project__description="Different wording of the same project.")
    assert choices(recommend(s)) == choices(outputs["P5"])


def test_bigger_budget_never_lowers_score():
    lo = recommend(variant("P3", budget__monthly_infra_usd=800))
    hi = recommend(variant("P3", budget__monthly_infra_usd=4000))
    assert hi["score"]["total"] >= lo["score"]["total"] - 1e-9


# hard constraints

def test_airgapped_has_no_saas():
    o = recommend(variant("P3", infrastructure__deployment_model="air_gapped"))
    check_schema(o)
    assert o["status"] == "ok_with_warnings"
    c = choices(o)
    assert "on-premise" in c["hosting"].lower() or "self-managed" in c["hosting"].lower()
    assert "AIRGAPPED_SAAS_REQUIREMENT" in codes(o)
    assert any(w["severity"] == "critical" for w in o["warnings"])


def test_onprem_database_self_hosted():
    o = recommend(variant("P3", infrastructure__deployment_model="on_premise"))
    c = choices(o)
    assert "Kubernetes" in c["hosting"] or "VM" in c["hosting"] or "on-prem" in c["hosting"].lower()
    assert c["database"] not in ("Amazon DynamoDB", "Cloud Firestore", "Azure Cosmos DB", "Neon (serverless Postgres)")


def test_vm_deployment_self_hosts_db():
    o = recommend(variant("P3", infrastructure__deployment_model="self_hosted_cloud_vm"))
    assert choices(o)["database"] not in ("Amazon DynamoDB", "Cloud Firestore", "Azure Cosmos DB")
    assert choices(o)["database"] != "Microsoft SQL Server / Azure SQL"


@pytest.mark.parametrize("cloud,words", [("aws", ("AWS", "Amazon", "Fargate", "Lambda", "Elastic", "Lightsail", "EC2", "App Runner")),
                                         ("gcp", ("Google", "Cloud Run", "GCE", "Firebase", "App Engine")),
                                         ("azure", ("Azure",))])
def test_cloud_preference(cloud, words):
    o = recommend(variant("P3", infrastructure__cloud_preference=cloud))
    h = choices(o)["hosting"]
    assert any(w in h for w in words), h


def test_residency_eu_hosting_has_region():
    o = recommend(variant("P3", infrastructure__data_residency="eu"))
    assert o["status"] in ("ok", "ok_with_warnings")
    assert "eu" in json.dumps(o["profile"]).lower()


def test_hipaa_flags_baa():
    o = recommend(variant("P3", compliance=["hipaa"], infrastructure__data_residency="us"))
    assert "HIPAA_BAA_NEEDED" in codes(o)


def test_compliance_filter_removes_uncertified():
    o = recommend(variant("P4", compliance=["hipaa", "pci_dss", "soc2"], infrastructure__data_residency="none"))
    assert o["status"] in ("ok", "ok_with_warnings", "infeasible")
    if o["stack"]:
        check_schema(o)


# infeasible and invalid

def test_hard_budget_infeasible():
    s = variant("P3", budget__monthly_infra_usd=5)
    s["extra"] = {"budget_strictness": "hard"}
    o = recommend(s)
    assert o["status"] == "infeasible"
    assert o["stack"] is None
    assert "BUDGET_INFEASIBLE" in codes(o)
    check_schema(o)


def test_invalid_inputs():
    for bad in ({}, "text", None, [], {"project": 5}):
        o = recommend(bad)
        assert o["status"] == "invalid_input"
        assert o["stack"] is None
        assert o["validation_errors"]
        check_schema(o)


def test_invalid_field_values():
    s = variant("P3", team__size=-4)
    s["requirements"]["auth"] = "telepathy"
    n, errs, _ = validate(s)
    assert errs
    o = recommend(s)
    assert o["status"] == "invalid_input"
    assert any("team" in json.dumps(e) for e in o["validation_errors"])


def test_missing_required_section():
    s = load("P2")
    del s["scale"]
    o = recommend(s)
    assert o["status"] == "invalid_input"


def test_validation_normalises_and_reports_assumptions():
    s = load("P1")
    s["team"]["size"] = "3"
    n, errs, ass = validate(s)
    assert isinstance(ass, list)
    assert n["team"]["size"] == 3 or errs


def test_peak_exceeds_users_is_flagged():
    s = variant("P1", scale__peak_concurrent_users=10 ** 7)
    o = recommend(s)
    assert o["status"] in ("invalid_input", "ok", "ok_with_warnings")
    check_schema(o)


def test_conflict_detection_budget_vs_scale():
    s = variant("P4", budget__monthly_infra_usd=50)
    n, errs, _ = validate(s)
    ws = detect(n)
    assert any(w["code"] == "BUDGET_TOO_LOW_FOR_SCALE" for w in ws)


def test_conflict_detection_deployment_cloud():
    s = variant("P3", infrastructure__deployment_model="on_premise", infrastructure__cloud_preference="aws")
    n, errs, _ = validate(s)
    assert any(w["code"] == "DEPLOYMENT_CLOUD_CONFLICT" for w in detect(n))


# knowledge base

def test_kb_valid():
    kb.load()
    assert kb.sources_all()


@pytest.mark.parametrize("layer", ["frontend", "backend", "database", "hosting"])
def test_kb_core_breadth(layer):
    assert len(kb.core(layer)) >= 8


def test_kb_entries_have_sources_and_rationale():
    for layer in ("frontend", "backend", "database", "hosting"):
        for e in kb.core(layer):
            assert e["sources"], e["id"]
            assert e["last_verified"], e["id"]
            for crit, v in e["scores"].items():
                assert 0 <= v["score"] <= 10 and v["why"]


def test_runtime_budget():
    import time
    t = time.time()
    recommend(load("P4"))
    assert time.time() - t < 10


# review fixes

def _codes(o):
    return {w["code"] for w in o["warnings"]}


def test_rbi_residency_conflict_is_critical():
    o = recommend(variant("P4", infrastructure__data_residency="eu"))
    assert "RBI_RESIDENCY_CONFLICT" in _codes(o)
    assert next(w for w in o["warnings"] if w["code"] == "RBI_RESIDENCY_CONFLICT")["severity"] == "critical"


def test_redis_managed_requires_saas():
    e = kb.get("redis_managed")
    assert e["requires_saas"] and not e["self_hostable"]


def test_hipaa_keeps_payments_layer():
    o = recommend(variant("P3", compliance=["hipaa"], requirements__payments=True))
    assert any(x["layer"] == "payments" for x in o["stack"]["supporting"])


def test_no_excluded_alternatives():
    o = recommend(load("P3"))
    alts = [a["why_not"] for c in o["stack"].values() if isinstance(c, dict) for a in c["alternatives"]]
    alts += [a["why_not"] for s in o["stack"]["supporting"] for a in s["alternatives"]]
    assert alts and not any(t.startswith("Excluded") for t in alts)


def test_timeline_warning_needs_real_gap():
    for sid in ("P1", "P6"):
        assert "TIMELINE_VS_PLATFORMS" not in _codes(recommend(load(sid)))


def test_empty_team_languages_warns():
    o = recommend(variant("P3", team__languages=[]))
    assert "SKILL_MISMATCH" in _codes(o)


def test_deprecated_and_integrations_have_sources():
    k = kb.load()
    assert all(e.get("source", "").startswith("http") for e in k["deprecated"] + k["integrations"])
    ids = {e["id"] for e in k["deprecated"]}
    assert {"sql_server_old", "mysql_57", "vue2", "php_old", "ubuntu_old", "postgres_old"} <= ids


def test_budget_at_risk_needs_midpoint_over_budget():
    o = recommend(load("P5"))
    band = o["estimated_monthly_cost_usd"]
    codes = _codes(o)
    mid = (band["min"] + band["max"]) / 2
    budget = o["profile"]["budget"] if "budget" in o["profile"] else None
    if budget and mid <= budget:
        assert "BUDGET_AT_RISK" not in codes


# round 2: supporting triggers, managed monitoring, data model, games, bands

def _sup(o):
    return {x["layer"]: x for x in o["stack"]["supporting"]}


def test_small_projects_get_no_cache_or_queue(outputs):
    for sid in ("P2", "P6"):
        s = _sup(outputs[sid])
        assert "cache" not in s and "queue" not in s
    assert any("No cache layer" in a for a in outputs["P6"]["assumptions"])


def test_included_cache_cites_its_trigger(outputs):
    j = _sup(outputs["P3"])["cache"]["justification"]
    assert "requests/second" in j or "users" in j


def test_no_devops_gets_managed_monitoring(outputs):
    for sid in ("P2", "P6"):
        o = _sup(outputs[sid])["observability"]
        assert "self-hosted" not in o["choice"]


def test_marketplace_prefers_relational_database(outputs):
    assert outputs["P2"]["stack"]["database"]["choice"] not in ("Cloud Firestore", "MongoDB Atlas", "Amazon DynamoDB")


def test_gaming_picks_engine_client_and_game_server():
    o = recommend(variant("P3", project__type="gaming"))
    c = choices(o)
    assert c["frontend"] in ("Unity", "Godot Engine", "Unreal Engine")
    assert c["backend"].split()[0] in ("Nakama", "Colyseus")


def test_collab_can_pick_crdt_for_devops_team(outputs):
    assert _sup(outputs["P3"])["realtime"]["choice"] == "Yjs with Hocuspocus"


def test_iot_and_analytics_cover_or_warn():
    o = recommend(variant("P3", project__type="data_analytics"))
    db = choices(o)["database"]
    assert "ClickHouse" in db or "BigQuery" in db or "PROJECT_TYPE_NOT_COVERED" in _codes(o)


def test_cost_bands_are_tight(outputs):
    for sid in ("P2", "P3", "P4", "P5"):
        b = outputs[sid]["estimated_monthly_cost_usd"]
        assert b["max"] <= 3.0 * b["min"]


# round 3: locality, hosting, archetypes, text relevance

def test_mobile_only_baas_has_no_hosting(outputs):
    assert outputs["P6"]["stack"]["hosting"]["choice"].startswith("None")


def test_more_budget_never_gives_pricier_stack():
    base = recommend(load("P3"))
    more = recommend(variant("P3", budget__monthly_infra_usd=1725))
    assert more["estimated_monthly_cost_usd"]["max"] <= base["estimated_monthly_cost_usd"]["max"] * 1.05


def test_india_context_survives_residency_none():
    o = recommend(variant("P2", infrastructure__data_residency="none"))
    s = _sup(o)
    assert s["sms"]["choice"] == "MSG91" and s["payments"]["choice"] == "Razorpay"


def test_timeseries_db_only_for_telemetry_projects():
    assert "TimescaleDB" in choices(recommend(variant("P3", project__type="iot", infrastructure__cloud_preference="aws")))["database"]
    o = recommend(variant("P3", project__type="internal_tool", scale__users_12_months=50, scale__users_at_launch=20, scale__peak_concurrent_users=10))
    assert "Timescale" not in choices(o)["database"]
    assert "PROJECT_TYPE_NOT_COVERED" not in _codes(recommend(variant("P3", project__type="iot")))


def test_static_site_has_no_cache_or_queue():
    o = recommend(variant("P1", scale__users_12_months=5000000, scale__users_at_launch=100000, scale__peak_concurrent_users=50000))
    assert not {"cache", "queue"} & set(_sup(o))
    assert o["estimated_monthly_cost_usd"]["max"] < 100


def test_zero_budget_gets_free_tier_note():
    assert "FREE_TIER_LIMITS" in _codes(recommend(variant("P1", budget__monthly_infra_usd=0)))


def test_irrelevant_kb_text_is_not_shown(outputs):
    text = json.dumps(outputs["P2"]["stack"])
    assert "Heavier on 2 GB" not in text and "Depends on native module" not in text
    p3 = json.dumps(outputs["P3"]["stack"])
    assert "UPI" not in p3 and "Tauri" not in p3


def test_over_budget_stack_is_repaired_with_cheaper_services(outputs):
    o = outputs["P4"]
    assert "BUDGET_EXCEEDED" not in _codes(o)
    assert o["estimated_monthly_cost_usd"]["min"] < 6000
    assert _sup(o)["auth"]["choice"] == "Keycloak"
    assert "small instance" in _sup(o)["ml"]["choice"]


def test_ledger_workload_prefers_spring_boot_for_jvm_team(outputs):
    assert outputs["P4"]["stack"]["backend"]["choice"] == "Java + Spring Boot"


def _mid(out):
    c = out["estimated_monthly_cost_usd"]
    return (c["min"] + c["max"]) / 2.0


@pytest.mark.parametrize("pid", ["P2", "P3"])
def test_lower_budget_never_picks_a_dearer_stack(pid):
    low = recommend(variant(pid, budget__monthly_infra_usd=0))
    high = recommend(variant(pid, budget__monthly_infra_usd=1000))
    assert _mid(low) <= _mid(high) * 1.05


def test_iot_prefers_a_time_series_store():
    out = recommend(variant("P3", project={**load("P3")["project"], "type": "iot"}))
    db = kb.load()["by_id"][out["decision_trace"]["layers"]["database"]["chosen"][0]]
    assert db["attrs"]["kind"] in ("timeseries", "analytics")


def test_time_series_store_is_not_offered_for_a_general_app(outputs):
    for o in outputs.values():
        if o["stack"]:
            assert o["decision_trace"]["layers"]["database"]["chosen"] != ["timescaledb"]


def test_single_vm_does_not_meet_four_nines_without_devops():
    from stacksense.derive import build_profile
    from stacksense.scoring import availability_level
    from stacksense import validate
    s = variant("P5", requirements={**load("P5")["requirements"], "availability": "99.99"})
    s["team"]["has_devops"] = False
    n, _, _ = validate.validate(s)
    p = build_profile(n)
    vm, managed = kb.load()["by_id"]["azure_vm"], kb.load()["by_id"]["azure_app_service"]
    assert availability_level(vm, p) < availability_level(managed, p) + 0.5
    assert availability_level(vm, p) < p["avail_level"]


def test_existing_systems_reuse_is_reported(outputs):
    codes = {w["code"] for w in outputs["P5"]["warnings"]}
    assert "EXISTING_SYSTEM_REUSED" in codes
    assert "AGING_EXISTING_SYSTEM" in codes


def test_timeline_message_mentions_platforms_only_when_several():
    s = variant("P1", timeline={"mvp_weeks": 1})
    s["project"]["type"] = "saas_b2b"
    for w in recommend(s)["warnings"]:
        if w["code"] == "TIMELINE_VS_PLATFORMS":
            assert "drop a platform" not in w["message"]


def test_static_site_on_free_static_host(outputs):
    assert outputs["P1"]["decision_trace"]["layers"]["hosting"]["chosen"] in (["cloudflare_pages"], ["github_pages"], ["netlify"])


def test_mobile_only_baas_app_gets_no_separate_host(outputs):
    assert outputs["P6"]["decision_trace"]["layers"]["hosting"]["chosen"] == ["none_host"]
    s = variant("P6", project__platforms=["android", "ios"])
    assert recommend(s)["decision_trace"]["layers"]["hosting"]["chosen"] == ["none_host"]


def test_description_signals_raise_field_warnings(outputs):
    codes = {w["code"] for w in outputs["P6"]["warnings"]}
    assert {"LOW_END_DEVICES", "WEAK_NETWORK", "LOCATION_DATA"} <= codes
    plain = variant("P6", project__description="Field agents fill crop surveys on Android phones.")
    codes = {w["code"] for w in recommend(plain)["warnings"]}
    assert not codes & {"LOW_END_DEVICES", "WEAK_NETWORK", "LOCATION_DATA"}


def test_tight_realtime_latency_is_flagged(outputs):
    assert "REALTIME_LATENCY" in {w["code"] for w in outputs["P3"]["warnings"]}
    loose = variant("P3", requirements={**load("P3")["requirements"], "p95_latency_ms": 800})
    assert "REALTIME_LATENCY" not in {w["code"] for w in recommend(loose)["warnings"]}


def test_region_is_named_for_residency_and_gdpr(outputs):
    assert "Mumbai" in outputs["P2"]["stack"]["database"]["justification"]
    assert "Central India" in outputs["P5"]["stack"]["hosting"]["justification"]
    assert "EU region" in outputs["P3"]["stack"]["hosting"]["justification"]
    us = recommend(variant("P3", compliance=[]))
    assert "EU region" not in us["stack"]["hosting"]["justification"]


def test_budget_warning_names_largest_items(outputs):
    for w in outputs["P2"]["warnings"]:
        if w["code"] == "BUDGET_AT_RISK":
            assert "largest items" in w["message"]


def test_billing_and_tenants_keep_a_relational_store_on_every_cloud():
    for cloud in ("aws", "gcp", "azure"):
        out = recommend(variant("P3", infrastructure={**load("P3")["infrastructure"], "cloud_preference": cloud}))
        db = kb.load()["by_id"][out["decision_trace"]["layers"]["database"]["chosen"][0]]
        assert db["attrs"]["kind"] == "relational", cloud


def test_cloud_preference_keeps_baas_without_separate_host():
    for cloud in ("aws", "gcp"):
        out = recommend(variant("P6", infrastructure={**load("P6")["infrastructure"], "cloud_preference": cloud}))
        chosen = out["decision_trace"]["layers"]
        assert kb.load()["by_id"][chosen["backend"]["chosen"][0]]["attrs"]["runtime"] == "baas", cloud
        assert chosen["hosting"]["chosen"][0] == "none_host", cloud


EDGE = os.path.join(ROOT, "scenarios", "edge")
EDGE_STATUS = {"E1": "ok_with_warnings", "E2": "infeasible", "E3": "infeasible", "E4": "ok_with_warnings",
               "E5": "invalid_input", "E6": "ok", "E7": "ok_with_warnings", "E8": "ok_with_warnings"}


def _edge(eid):
    name = [f for f in os.listdir(EDGE) if f.startswith(eid + "_")][0]
    with open(os.path.join(EDGE, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("eid", sorted(EDGE_STATUS))
def test_edge_scenarios_give_expected_status(eid):
    out = recommend(_edge(eid))
    assert out["status"] == EDGE_STATUS[eid]
    for w in out["warnings"]:
        assert w["severity"] in SEVERITIES


def test_robustness_shape_and_determinism(outputs, scenarios):
    for pid in IDS:
        rob = outputs[pid]["robustness"]
        assert rob["verdict"] in {"stable", "mostly stable", "sensitive"}
        assert rob["checks_run"] == len(rob["checks"]) >= 12
        assert rob["checks_held"] == sum(1 for c in rob["checks"] if c["same_stack"])
        for c in rob["checks"]:
            if not c["same_stack"]:
                assert c.get("swaps") or c.get("result")
    again = recommend(copy.deepcopy(scenarios["P4"]))
    assert again["robustness"] == outputs["P4"]["robustness"]
    assert again["trade_off_options"] == outputs["P4"]["trade_off_options"]


def test_trade_off_options_stay_close_and_do_better_in_their_direction(outputs):
    for pid in IDS:
        for o in outputs[pid]["trade_off_options"]:
            if not o["available"]:
                continue
            assert 0 <= o["points_behind"] <= 12
            assert o["swaps"] and o["summary"]
            if o["direction"] == "cheaper":
                mine = outputs[pid]["ranking"][0]["cost_usd"]
                assert sum(o["cost_usd"]) <= 0.8 * sum(mine) + 1e-6


def test_infeasible_input_offers_single_change_repairs():
    e2 = recommend(_edge("E2"))
    fields = {f["field"] for f in e2["repairs"]}
    assert {"infrastructure.cloud_preference", "infrastructure.deployment_model"} <= fields
    assert sum(1 for w in e2["warnings"] if w["code"] == "FEASIBLE_IF_CHANGED") == len(e2["repairs"])
    e3 = recommend(_edge("E3"))
    assert any(f["field"] == "extra.budget_strictness" for f in e3["repairs"])
    for f in e2["repairs"] + e3["repairs"]:
        assert set(f["stack"]) == {"frontend", "backend", "database", "hosting"}


def test_owned_windows_licences_shift_to_iis():
    base = _edge("E1")
    assert recommend(copy.deepcopy(base))["stack"]["hosting"]["choice"] != "Windows Server + IIS (self-hosted)"
    base.setdefault("extra", {})["existing_licenses"] = ["windows_server"]
    out = recommend(base)
    assert out["stack"]["hosting"]["choice"] == "Windows Server + IIS (self-hosted)"
    assert "LICENCE_REUSED" in {w["code"] for w in out["warnings"]}


def test_node_backends_are_not_paired_with_iis():
    for eid in ("E1", "E2"):
        out = recommend(_edge(eid))
        stacks = [r["stack"] for r in out.get("ranking", [])[:3]] + [f["stack"] for f in out.get("repairs", [])]
        for s in stacks:
            if s["hosting"] == "Windows Server + IIS (self-hosted)":
                assert not s["backend"].startswith("Node.js")


def test_free_ci_seats_are_not_billed():
    out = recommend(_edge("E6"))
    assert out["estimated_monthly_cost_usd"]["max"] == 0
    assert "BUDGET_TOO_LOW_FOR_SCALE" not in {w["code"] for w in out["warnings"]}


def test_report_includes_firmness_options_and_repairs(outputs):
    import io
    from pypdf import PdfReader
    from stacksense.report import build_pdf

    def text(r):
        return " ".join(pg.extract_text() for pg in PdfReader(io.BytesIO(build_pdf(r))).pages)

    ok = text(outputs["P4"])
    assert "How firm the pick is" in ok and "If you want something different" in ok
    assert "Single changes that make a stack possible" in text(recommend(_edge("E2")))


def _with_credits(pid, licences, amount=None):
    extra = dict(load(pid).get("extra") or {}, existing_licenses=licences)
    if amount is not None:
        extra["cloud_credits_usd"] = amount
    return variant(pid, extra=extra)


def test_large_credits_tip_a_near_tie_and_small_ones_do_not(outputs):
    assert choices(outputs["P3"])["hosting"] == "Google Cloud Run"
    small = recommend(_with_credits("P3", ["azure_credits"], 200))
    assert choices(small)["hosting"] == "Google Cloud Run"
    assert "do not apply" in [w for w in small["warnings"] if w["code"] == "CLOUD_CREDITS"][0]["message"]
    big = recommend(_with_credits("P3", ["azure_credits"], 20000))
    assert choices(big)["hosting"] == "Azure Container Apps"
    cr = big["estimated_monthly_cost_usd"]["credits"]
    assert cr["provider"] == "Azure" and cr["months_covered"] > 6
    assert cr["first_year_average_usd"][1] < big["estimated_monthly_cost_usd"]["max"]


def test_credits_report_coverage_without_changing_list_price(outputs):
    out = recommend(_with_credits("P4", ["aws_credits"], 20000))
    assert choices(out) == choices(outputs["P4"])
    band = out["estimated_monthly_cost_usd"]
    assert (band["min"], band["max"]) == (outputs["P4"]["estimated_monthly_cost_usd"]["min"], outputs["P4"]["estimated_monthly_cost_usd"]["max"])
    lo, hi = band["credits"]["covered_usd"]
    assert 0 < lo <= hi <= band["max"]
    no_amount = recommend(_with_credits("P4", ["aws_credits"]))
    assert "months_covered" not in no_amount["estimated_monthly_cost_usd"]["credits"]


def test_credits_on_other_cloud_are_reported_as_not_applying():
    out = recommend(_with_credits("P1", ["aws_credits"], 1000))
    msg = [w for w in out["warnings"] if w["code"] == "CLOUD_CREDITS"][0]["message"]
    assert "do not apply" in msg and "credits" not in out["estimated_monthly_cost_usd"]


def test_overengineering_note_only_when_complexity_explains_the_gap(outputs):
    for pid in IDS:
        notes = [w for w in outputs[pid]["warnings"] if w["code"] == "OVERENGINEERING_AVOIDED"]
        for w in notes:
            assert w["severity"] == "info" and "points behind" in w["message"]
    assert any(w["code"] == "OVERENGINEERING_AVOIDED" for w in recommend(_edge("E4"))["warnings"])
    assert not any(w["code"] == "OVERENGINEERING_AVOIDED" for w in outputs["P4"]["warnings"])
