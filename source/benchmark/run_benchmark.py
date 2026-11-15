"""Score the engine against benchmark/briefs.json.

Each brief has an answer key per core layer: "best" (what an experienced architect would most likely pick)
and "acceptable" (defensible alternatives). A frontend pair such as "expo_rn+nextjs" must match as a whole.
Usage, from source/:  python benchmark/run_benchmark.py [briefs.json|holdout.json] [--json out.json]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stacksense import kb  # noqa: E402
from stacksense.engine import recommend  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NONE_ID = {"frontend": "none_fe", "backend": "none_be", "database": "none_db", "hosting": "none_host"}


def picked(out, layer):
    c = (out.get("stack") or {}).get(layer) or {}
    return c.get("kb_id") or NONE_ID[layer]


def same(a, b):
    return sorted(a.split("+")) == sorted(b.split("+"))


def grade(pick, key):
    if any(same(pick, x) for x in key["best"]):
        return "best"
    if any(same(pick, x) for x in key.get("acceptable", [])):
        return "acceptable"
    return "miss"


def check_key(briefs):
    ids = {e["id"] for layer in kb.CORE_LAYERS for e in kb.core(layer)}
    bad = sorted({part for b in briefs for k in b["key"].values() for x in k["best"] + k.get("acceptable", [])
                  for part in x.split("+") if part not in ids})
    if bad:
        raise SystemExit("unknown ids in answer key: %s" % ", ".join(bad))


def run(name="briefs.json"):
    data = json.load(open(os.path.join(HERE, os.path.basename(name)), encoding="utf-8"))
    briefs = data["briefs"]
    check_key(briefs)
    rows = []
    for b in briefs:
        out = recommend(b["scenario"])
        grades = {layer: grade(picked(out, layer), b["key"][layer]) for layer in kb.CORE_LAYERS}
        rows.append({"id": b["scenario"]["scenario_id"], "name": b["scenario"]["project"]["name"], "status": out["status"],
                     "picks": {layer: picked(out, layer) for layer in kb.CORE_LAYERS}, "grades": grades})
    n = len(rows)
    layers = {layer: {"best": sum(r["grades"][layer] == "best" for r in rows),
                      "acceptable_or_best": sum(r["grades"][layer] != "miss" for r in rows)} for layer in kb.CORE_LAYERS}
    summary = {
        "briefs": n,
        "layer_decisions": n * len(kb.CORE_LAYERS),
        "best_layer_hits": sum(v["best"] for v in layers.values()),
        "acceptable_layer_hits": sum(v["acceptable_or_best"] for v in layers.values()),
        "whole_stack_acceptable": sum(all(g != "miss" for g in r["grades"].values()) for r in rows),
        "whole_stack_best": sum(all(g == "best" for g in r["grades"].values()) for r in rows),
        "per_layer": layers,
    }
    return summary, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default="briefs.json")
    ap.add_argument("--json")
    a = ap.parse_args()
    summary, rows = run(a.file)
    d = summary["layer_decisions"]
    for r in rows:
        marks = " ".join("%s=%s(%s)" % (k[:2], r["picks"][k], r["grades"][k][0]) for k in kb.CORE_LAYERS)
        print("%s %-18s %-17s %s" % (r["id"], r["name"][:18], r["status"], marks))
    print()
    print("Layer decisions: %d. Matched the expected pick: %d (%.0f%%). Expected or acceptable: %d (%.0f%%)." % (
        d, summary["best_layer_hits"], 100.0 * summary["best_layer_hits"] / d,
        summary["acceptable_layer_hits"], 100.0 * summary["acceptable_layer_hits"] / d))
    print("Whole stacks with every layer acceptable: %d of %d. Every layer the expected pick: %d of %d." % (
        summary["whole_stack_acceptable"], summary["briefs"], summary["whole_stack_best"], summary["briefs"]))
    for k, v in summary["per_layer"].items():
        print("  %-9s expected %2d, acceptable %2d of %d" % (k, v["best"], v["acceptable_or_best"], summary["briefs"]))
    if a.json:
        json.dump({"summary": summary, "rows": rows}, open(a.json, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
