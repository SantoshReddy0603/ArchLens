import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from stacksense.engine import recommend  # noqa: E402

SCENARIOS = os.path.join(ROOT, "scenarios")
IDS = ["P1", "P2", "P3", "P4", "P5", "P6"]


def load(pid):
    with open(os.path.join(SCENARIOS, pid + ".json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def scenarios():
    return {i: load(i) for i in IDS}


@pytest.fixture(scope="session")
def outputs(scenarios):
    return {i: recommend(copy.deepcopy(s)) for i, s in scenarios.items()}


def variant(pid, **sections):
    """Copy of a scenario with whole keys replaced, e.g. variant("P3", infrastructure={...}) or key paths
    written section__key."""
    s = copy.deepcopy(load(pid))
    for k, v in sections.items():
        if "__" in k:
            a, b = k.split("__")
            s[a][b] = v
        else:
            s[k] = v
    return s


def choices(out):
    st = out["stack"]
    return {l: st[l]["choice"] for l in ("frontend", "backend", "database", "hosting")}


def support_ids(out):
    return {x["layer"]: x["choice"] for x in out["stack"]["supporting"]}
