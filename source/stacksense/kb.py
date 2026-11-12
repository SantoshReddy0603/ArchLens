"""Knowledge base loader. Reads kb/*.json once, validates the shape and exposes sorted, deterministic views."""
import json
import os
import re

KB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb")
CORE_LAYERS = ("frontend", "backend", "database", "hosting")
CRITERIA = ("cost", "time_to_market", "scalability", "performance", "maintainability", "security")
_CORE_KEYS = ("id", "name", "layer", "scores", "maturity", "ops_burden", "complexity", "sources", "last_verified")
_SUP_KEYS = ("id", "name", "layer", "quality", "cost", "sources", "last_verified")
_cache = {}


class KBError(ValueError):
    pass


def _read(name):
    with open(os.path.join(KB_DIR, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def check(entries, keys, name):
    """Raise KBError listing the first structural problem of a KB file."""
    seen = set()
    for e in entries:
        for k in keys:
            if k not in e:
                raise KBError("%s: entry %r lacks %r" % (name, e.get("id"), k))
        if e["id"] in seen:
            raise KBError("%s: duplicate id %r" % (name, e["id"]))
        seen.add(e["id"])
        if not e["sources"] or not all(s.get("url", "").startswith("http") for s in e["sources"]):
            raise KBError("%s: entry %r needs source urls" % (name, e["id"]))
        if "scores" in e:
            for c in CRITERIA:
                s = e["scores"].get(c)
                if not s or not 0 <= s["score"] <= 10 or not s["why"]:
                    raise KBError("%s: entry %r has a bad %s score" % (name, e["id"], c))


def load():
    """Return the whole KB as a dict (cached). Keys: core (layer -> list), supporting (layer -> list),
    by_id, deprecated, integrations."""
    if "kb" in _cache:
        return _cache["kb"]
    core = {}
    by_id = {}
    for layer in CORE_LAYERS:
        entries = _read(layer)
        check(entries, _CORE_KEYS, layer)
        core[layer] = sorted(entries, key=lambda e: e["id"])
        for e in entries:
            by_id[e["id"]] = e
    sup_entries = _read("supporting")
    check(sup_entries, _SUP_KEYS, "supporting")
    supporting = {}
    for e in sorted(sup_entries, key=lambda e: e["id"]):
        supporting.setdefault(e["layer"], []).append(e)
        by_id[e["id"]] = e
    deprecated = _read("deprecated")
    integrations = _read("integrations")
    for d in deprecated:
        d["_re"] = re.compile(d["pattern"], re.I)
    for i in integrations:
        i["_re"] = re.compile(i["pattern"], re.I)
    kb = {"core": core, "supporting": supporting, "by_id": by_id, "deprecated": deprecated, "integrations": integrations}
    _cache["kb"] = kb
    return kb


def core(layer):
    return load()["core"][layer]


def supporting(layer):
    return load()["supporting"].get(layer, [])


def get(comp_id):
    return load()["by_id"].get(comp_id)


def sources_all():
    """Every distinct source URL with its title, sorted by URL."""
    out = {}
    for e in load()["by_id"].values():
        for s in e["sources"]:
            out[s["url"]] = s["title"]
    return sorted(out.items())
