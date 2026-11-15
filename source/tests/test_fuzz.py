"""Property checks on seeded random scenarios: valid input never crashes or comes back invalid, explicit hard
constraints hold or are flagged critical, and costs and core layers are well formed."""
import copy
import json
import random

import pytest

from stacksense import kb
from stacksense.engine import recommend
from stacksense.validate import ENUMS, PRIORITY_KEYS, BOOL_REQUIREMENTS

LANGS = ["typescript", "javascript", "python", "go", "java", "csharp", "kotlin", "php", "ruby", "rust", "dart"]
SYSTEMS = ["PostgreSQL 16", "MySQL 5.7", "Microsoft 365", "Entra ID", "SQL Server 2014", "Salesforce", "Python 2.7"]
OK = ("ok", "ok_with_warnings", "infeasible")


def scenario(seed):
    r = random.Random(seed)
    users = r.choice([0, 50, 500, 5000, 50000, 500000, 5000000])
    s = {
        "scenario_id": "F%d" % seed,
        "project": {"name": "Fuzz %d" % seed, "type": r.choice(ENUMS["project.type"]),
                    "description": "Generated scenario",
                    "platforms": r.sample(ENUMS["project.platforms"][:4], r.randint(1, 3))},
        "scale": {"users_at_launch": users, "users_12_months": users * r.choice([1, 3, 10]),
                  "peak_concurrent_users": max(1, users // r.choice([5, 20, 100])),
                  "traffic_pattern": r.choice(ENUMS["scale.traffic_pattern"]),
                  "workload": r.choice(ENUMS["scale.workload"]),
                  "data_volume_gb_12_months": r.choice([1, 20, 200, 2000])},
        "team": {"size": r.choice([1, 2, 4, 8, 25]), "experience": r.choice(ENUMS["team.experience"]),
                 "languages": r.sample(LANGS, r.randint(1, 3)), "has_devops": r.random() < 0.5},
        "budget": {"monthly_infra_usd": r.choice([0, 20, 100, 500, 2000, 20000])},
        "timeline": {"mvp_weeks": r.choice([2, 6, 12, 26])},
        "infrastructure": {"cloud_preference": r.choice(ENUMS["infrastructure.cloud_preference"]),
                           "deployment_model": r.choice(ENUMS["infrastructure.deployment_model"]),
                           "data_residency": r.choice(ENUMS["infrastructure.data_residency"]),
                           "existing_systems": r.sample(SYSTEMS, r.randint(0, 2))},
        "requirements": dict({k: r.random() < 0.3 for k in BOOL_REQUIREMENTS},
                             auth=r.choice(ENUMS["requirements.auth"]),
                             ml_features=r.choice(ENUMS["requirements.ml_features"]),
                             availability=r.choice(ENUMS["requirements.availability"]),
                             p95_latency_ms=r.choice([None, 100, 300, 1000])),
        "compliance": r.sample(ENUMS["compliance"], r.randint(0, 2)),
        "priorities": {k: r.randint(1, 5) for k in PRIORITY_KEYS},
    }
    return s


SEEDS = list(range(150))


@pytest.fixture(scope="module")
def runs():
    return {seed: (scenario(seed), recommend(scenario(seed))) for seed in SEEDS}


def _critical(o):
    return [w["code"] for w in o["warnings"] if w["severity"] == "critical"]


def test_valid_random_input_is_never_rejected(runs):
    for seed, (s, o) in runs.items():
        assert o["status"] in OK, (seed, o["status"], o.get("validation_errors"))


def test_feasible_plans_fill_every_core_layer(runs):
    for seed, (s, o) in runs.items():
        if o["status"] == "infeasible":
            assert _critical(o), seed
            continue
        for layer in kb.CORE_LAYERS:
            c = o["stack"][layer]
            assert c["choice"] and c["justification"] and c["driven_by"], (seed, layer)
        cost = o["estimated_monthly_cost_usd"]
        assert 0 <= cost["min"] <= cost["max"], seed


def test_explicit_cloud_is_kept_or_flagged(runs):
    for seed, (s, o) in runs.items():
        cloud = s["infrastructure"]["cloud_preference"]
        if o["status"] == "infeasible" or cloud not in ("aws", "gcp", "azure"):
            continue
        host = kb.get(o["stack"]["hosting"].get("kb_id") or "")
        if host is None or host["id"] == "none_host" or "provider" not in host.get("attrs", {}):
            continue
        assert host["attrs"]["provider"] == cloud or _critical(o), (seed, cloud, host["id"])


def test_residency_names_a_region_or_is_flagged(runs):
    for seed, (s, o) in runs.items():
        res = s["infrastructure"]["data_residency"]
        if o["status"] == "infeasible" or res not in ("india", "eu", "us"):
            continue
        db = o["stack"]["database"]
        if db.get("kb_id") in (None, "none_db") or s["infrastructure"]["deployment_model"] != "managed_cloud":
            continue
        text = json.dumps(db)
        assert db.get("region") or "self-hosted" in text.lower() or _critical(o), (seed, res, db["choice"])


def test_random_input_is_deterministic(runs):
    for seed in SEEDS[:5]:
        s, o = runs[seed]
        assert json.dumps(recommend(copy.deepcopy(s)), sort_keys=True) == json.dumps(o, sort_keys=True), seed


def test_unreadable_extra_value_is_ignored_not_rejected():
    s = scenario(3)
    s["extra"] = {"data_sensitivity": "restricted", "learning_appetite": 7}
    o = recommend(s)
    assert o["status"] in OK
    flagged = [w for w in o["warnings"] if w["code"] == "EXTRA_VALUE_IGNORED"]
    assert sorted(w["fields"][0] for w in flagged) == ["extra.data_sensitivity", "extra.learning_appetite"]
