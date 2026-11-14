"""Stack enumeration, pruning and deterministic ranking."""
import itertools

from . import kb
from .constraints import db_mode, filter_layer, is_none, platform_cover, runtime_ok, stack_reasons
from .scoring import Ctx, apply_budget_floor, score_stack
from . import supporting as sup

MIN_PLATFORM_QUALITY = 0.5
POOL_PER_PLATFORM = 4
SEO_STRONG = 7.0

PRUNE_KINDS = (("cannot run on", "runtime not supported by the host"), ("works only with", "frontend needs a specific backend"),
               ("is part of", "backend bundled with a specific frontend"), ("cannot be used with", "database tied to another backend"),
               ("needs ", "backend bundled with a specific database"), ("cannot be hosted", "database cannot be reached from the host"),
               ("deployment needs the database", "database must run on your own host"), ("self-managed database", "database must be self-managed"),
               ("does not run on", "database does not run on the chosen cloud"), ("is not on", "database not on the chosen cloud"),
               ("region", "no region in the required residency"), ("attestation", "missing compliance attestation"))


def _kind(reason):
    for key, label in PRUNE_KINDS:
        if key in reason:
            return label
    return "other incompatibility"


def _fe_rank_key(p, c):
    w = p["weights"]
    return -sum(w[k] * c["scores"][k]["score"] for k in w) / 10.0


def frontend_options(p, kept, eliminated):
    """Single frontends covering every platform, or pairs when none does. Updates the eliminated list."""
    plats = p["client_platforms"]
    if not plats:
        return [[c] for c in kept if is_none(c)], [], False
    real = [c for c in kept if not is_none(c)]
    covers = {c["id"]: platform_cover([c], plats) for c in real}
    singles = [c for c in real if all(q >= MIN_PLATFORM_QUALITY for q in covers[c["id"]].values())]
    seo_split = bool(p["seo"] and "web" in plats and len(plats) > 1 and not any(c["attrs"]["seo"] >= SEO_STRONG for c in singles))
    native_split = p.get("mobile_preference") == "native" and set(plats) == {"android", "ios"}
    if singles:
        if not native_split:
            for c in real:
                if c not in singles:
                    weak = [pl for pl, q in covers[c["id"]].items() if q < MIN_PLATFORM_QUALITY]
                    eliminated.append({"id": c["id"], "name": c["name"], "reasons": ["does not serve %s well enough on its own" % "/".join(weak)]})
        if not seo_split and not native_split:
            return [[c] for c in singles], [], False
    pool = {}
    for pl in plats:
        ranked = sorted((c for c in real if covers[c["id"]][pl] >= MIN_PLATFORM_QUALITY),
                        key=lambda c: (p["project_type"] != "content_site" and c["attrs"]["app_ui"] < 0.6, -(c["attrs"]["seo"] if p["seo"] and pl == "web" else 0), -covers[c["id"]][pl], _fe_rank_key(p, c), c["id"]))
        for c in ranked[:POOL_PER_PLATFORM]:
            pool[c["id"]] = c
    pairs = []
    for a, b in itertools.combinations(sorted(pool.values(), key=lambda c: c["id"]), 2):
        cov = platform_cover([a, b], plats)
        if not all(q >= MIN_PLATFORM_QUALITY for q in cov.values()):
            continue
        if seo_split and max(a["attrs"]["seo"], b["attrs"]["seo"]) < SEO_STRONG:
            continue
        pairs.append([a, b])
    used = {c["id"] for pr in pairs for c in pr}
    for c in real:
        if c["id"] not in used and c not in singles:
            eliminated.append({"id": c["id"], "name": c["name"], "reasons": ["not strong enough on any required platform to be part of a pair"]})
    return ([[c] for c in singles] if singles else []) + pairs, pairs, bool(pairs)


def enumerate_stacks(p):
    """Build, filter and score every compatible stack. Returns (ranked, info)."""
    info = {"layers": {}, "pruned": {}, "budget_eliminated": 0, "stacks_considered": 0}
    kept = {}
    for layer in kb.CORE_LAYERS:
        k, e = filter_layer(layer, kb.core(layer), p)
        kept[layer], info["layers"][layer] = k, {"candidates": len(kb.core(layer)), "eliminated": e}
    fe_opts, pairs, paired = frontend_options(p, kept["frontend"], info["layers"]["frontend"]["eliminated"])
    info["frontend_paired"] = paired
    ctx = Ctx(p, sup)
    results = []
    prune = {}
    for be, host in itertools.product(kept["backend"], kept["hosting"]):
        if not runtime_ok(be, host):
            k = "runtime not supported by the host"
            prune[k] = prune.get(k, 0) + len(kept["database"]) * len(fe_opts)
            continue
        for db in kept["database"]:
            mode_cache = None
            for fe in fe_opts:
                info["stacks_considered"] += 1
                r = stack_reasons(fe, be, db, host, p)
                if r:
                    k = _kind(r[0])
                    prune[k] = prune.get(k, 0) + 1
                    continue
                mode = db_mode(db, be, host, p)
                d = score_stack(p, ctx, fe, be, db, host, mode)
                if p["budget_strictness"] == "hard" and d["cost"][0] > p["budget"]:
                    info["budget_eliminated"] += 1
                    continue
                results.append({"fe": fe, "be": be, "db": db, "host": host, "mode": mode, "detail": d})
    info["pruned"] = dict(sorted(prune.items()))
    info["budget_floor"] = apply_budget_floor(results, p)
    results.sort(key=lambda r: (-round(r["detail"]["score"], 9), round(r["detail"]["complexity"], 9), round(r["detail"]["ops_load"], 9),
                                "+".join(c["id"] for c in r["fe"]), r["be"]["id"], r["db"]["id"], r["host"]["id"]))
    if results:
        pick = winner(results, cost=credit_cost(p))
        if pick is not results[0]:
            results.remove(pick)
            results.insert(0, pick)
    info["ctx"] = ctx
    return results, info


TIE_POINTS = 0.75
TIE_COST = 1.06


def _cost_mid(r):
    lo, hi = r["detail"]["cost"]
    return 0.35 * lo + 0.65 * hi


CREDIT_CLOUDS = ("aws", "gcp", "azure")


def credit_providers(p):
    return sorted({x[:-len("_credits")] for x in p.get("licenses", []) if x.endswith("_credits") and x[:-len("_credits")] in CREDIT_CLOUDS})


def core_billed_mid(r, prov):
    """Mid monthly cost of the core lines that `prov` bills: its hosting, a database run on that host, a provider-native service."""
    host = r["host"]["attrs"].get("provider")
    total = 0.0
    for layer, _, lo, hi in r["detail"]["cost_items"]:
        comp = {"hosting": r["host"], "database": r["db"], "backend": r["be"]}.get(layer)
        if comp is None:
            continue
        if layer == "hosting":
            by = host
        elif layer == "database" and r["mode"] in ("managed_native", "self_hosted"):
            by = host
        else:
            by = comp.get("billed_by")
        if by == prov:
            total += 0.35 * lo + 0.65 * hi
    return total


def credit_cost(p):
    """Near-tie cost measure net of stated cloud credits (spread over the first year), or None without an amount."""
    held, amount = credit_providers(p), p.get("cloud_credits_usd") or 0
    if not held or amount <= 0:
        return None

    def cost(r):
        host = r["host"]["attrs"].get("provider")
        prov = host if host in held else held[0]
        return max(0.0, _cost_mid(r) - min(amount / 12.0, core_billed_mid(r, prov)))
    return cost


def _tie_key(r, score=None):
    """Near-tied stacks within TIE_COST of the cheapest one compete on score, then simplicity (deterministic)."""
    s = r["detail"]["score"] if score is None else score(r)
    return (-round(s, 9), round(r["detail"]["complexity"], 9), round(r["detail"]["ops_load"], 9),
            "+".join(c["id"] for c in r["fe"]), r["be"]["id"], r["db"]["id"], r["host"]["id"])


def winner(results, score=None, cost=None):
    """The stack that wins under `score` (default: the stored score): the best score, except that among stacks
    within TIE_POINTS of it the cheapest (within TIE_COST) wins, then simplicity and ids break ties."""
    sc = (lambda r: r["detail"]["score"]) if score is None else score
    top = max(sc(r) for r in results)
    cm = _cost_mid if cost is None else cost
    near = [r for r in results if top - sc(r) < TIE_POINTS]
    cmin = min(cm(r) for r in near)
    return min((r for r in near if cm(r) <= TIE_COST * cmin), key=lambda r: _tie_key(r, sc))


def key(r):
    return "+".join(c["id"] for c in r["fe"]), r["be"]["id"], r["db"]["id"], r["host"]["id"]
