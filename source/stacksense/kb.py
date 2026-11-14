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
    offerings = _read("offerings")
    for group in ("database", "supporting"):
        for eid, per in offerings[group].items():
            for prov, o in per.items():
                if not o.get("name") or not o.get("url", "").startswith("http"):
                    raise KBError("offerings: %s/%s needs a name and a source url" % (eid, prov))
    kb = {"core": core, "supporting": supporting, "by_id": by_id, "deprecated": deprecated, "integrations": integrations,
          "offerings": offerings}
    _cache["kb"] = kb
    return kb


def core(layer):
    return load()["core"][layer]


def supporting(layer):
    return load()["supporting"].get(layer, [])


def get(comp_id):
    return load()["by_id"].get(comp_id)


def engine_name(comp):
    """Product name without the deployment qualifier, e.g. 'PostgreSQL' for 'PostgreSQL (managed or self-hosted)'."""
    return comp["name"].split(" (")[0].split(" / ")[0]


def offering(comp, host, mode=None):
    """The concrete managed product a generic engine runs as on this host, or None when the KB name is already
    the product. Returns {name, url, provider, self_hosted}."""
    if comp is None or host is None or comp["id"].startswith("none") or host["id"] == "none_host":
        return None
    table = load()["offerings"]["database" if comp["layer"] == "database" else "supporting"].get(comp["id"])
    prov = host["attrs"].get("provider")
    if comp["layer"] == "database" and mode == "self_hosted":
        if comp["id"] == "sqlite":
            return None
        return {"name": "%s, self-hosted on %s" % (engine_name(comp), host["name"].split(" (")[0].split(" / ")[0]), "url": None,
                "provider": prov, "self_hosted": True}
    if not table:
        return None
    o = table.get(prov) if mode != "managed_external" else None
    if o is None and comp["layer"] == "database" and mode == "managed_external":
        o = table.get("any")
    if o is None and comp["layer"] != "database":
        o = table.get(prov)
    if o is None:
        return None
    return {"name": o["name"], "url": o["url"], "provider": prov if prov in table else None, "self_hosted": False}


def sources_all():
    """Every distinct source URL with its title, sorted by URL."""
    out = {}
    for e in load()["by_id"].values():
        for s in e["sources"]:
            out[s["url"]] = s["title"]
    for group in ("database", "supporting"):
        for per in load()["offerings"][group].values():
            for o in per.values():
                out.setdefault(o["url"], o["name"])
    return sorted(out.items())
