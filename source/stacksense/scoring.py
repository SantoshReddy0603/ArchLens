"""Stack scoring.

  score = 100 * (0.55 * Q + 0.45 * F) - penalties
  Q  quality: weighted criterion scores of the four layers (0-1), weights from the project's priorities
  F  fit: skills, capability coverage, project-type archetype, operations load, budget, compatibility (0-1)
  penalties  points for over-engineering, under-provisioning, lock-in, hiring difficulty, immaturity,
             availability gap, missing soft certifications, poor SEO and a second codebase; reuse of
             existing systems is a small bonus.
Everything is a smooth function of the profile, so small input changes move scores a little."""
import math

from . import cost as costmod
from . import kb
from .constraints import db_mode, is_none, platform_cover
from .derive import reuse_source, skill_fit_component, _clamp
from .kb import CRITERIA

RELATIONAL_TYPES = ("marketplace", "fintech", "saas_b2b", "ecommerce", "healthtech")

FIT_WEIGHTS = {"skill": 0.28, "capability": 0.22, "archetype": 0.08, "ops": 0.14, "budget": 0.18, "compat": 0.10}
STRICTNESS = {"flexible": 0.7, "firm": 1.0, "hard": 1.6}
HIRING = {"none": 0.3, "slow": 0.6, "aggressive": 1.5}
LOCK = {"low": 0.9, "medium": 0.45, "high": 0.1}
ADDON = 0.85
SOFT_CERTS = ("soc2", "iso27001", "gdpr")


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _fe(fe_parts, f):
    return _mean(f(x) for x in fe_parts)


def web_seo(fe_parts):
    """SEO strength (0-1) of the best part that serves the web."""
    return max([c["attrs"]["seo"] for c in fe_parts if c["attrs"]["platform_quality"].get("web", 0) >= 0.5] or [0]) / 10.0


def web_part(fe_parts):
    """The frontend part that serves the web (best web quality, then SEO)."""
    return max(fe_parts, key=lambda c: (c["attrs"]["platform_quality"].get("web", 0), c["attrs"]["seo"], c["id"]))


def _crit(comp, k, cap):
    s = comp["scores"][k]["score"]
    return min(s, cap) if k == "scalability" else s


def quality(p, fe_parts, be, db, host):
    """Q in [0,1] and the per-criterion layer-weighted scores."""
    lw = p["layer_weights"]
    tot = sum(lw.values())
    cap = p["scale_need"] + 2.0
    per = {}
    for k in CRITERIA:
        fe_k = _fe(fe_parts, lambda c: _crit(c, k, cap))
        db_k = _crit(db, k, min(cap, db["attrs"].get("ceiling", 10.0)))
        per[k] = (lw["frontend"] * fe_k + lw["backend"] * _crit(be, k, cap) + lw["database"] * db_k
                  + lw["hosting"] * _crit(host, k, cap)) / tot
    q = sum(p["weights"][k] * per[k] / 10.0 for k in CRITERIA)
    return q, per




class Ctx:
    """Per-profile precomputed facts shared by every stack evaluation."""

    def __init__(self, p, sup):
        self.p = p
        self.sup = sup
        self.reserve = sup.reserve_costs(p)
        self.has = {l: bool(sup.feasible(l, p)) for l in ("auth", "payments", "search", "realtime", "storage", "ml", "sync")}
        self.sync = [e for e in sup.feasible("sync", p)]
        self.integr = [i for i in kb.load()["integrations"] if reuse_source(i, p)]


def _host_realtime(be, host):
    if be["attrs"]["runtime"] == "baas":
        return 1.0
    a = host["attrs"]
    if a["static_only"]:
        return 0.0
    if "container" in a["runtimes"] or a["environment"] in ("vm", "onprem"):
        return 1.0
    return 0.3


def coverage(p, ctx, fe_parts, be, db, host):
    """Weighted share of the project's functional needs the stack covers (natively or via a supporting service)."""
    caps, dcaps = be["capabilities"], db["capabilities"]
    needs = []
    plats = p["client_platforms"]
    if plats:
        q = platform_cover(fe_parts, plats)
        needs.append((1.5, _mean(q.values()), "platform quality"))
        needs.append((1.5, _fe(fe_parts, lambda c: 1.0 if p["project_type"] in c["strong_types"] else 0.1 if p["project_type"] in c["weak_types"] else 0.6), "app-type suitability"))
    if p["realtime"]:
        native = min(max(caps.get("realtime", 0), dcaps.get("realtime", 0)), _host_realtime(be, host))
        needs.append((1.2, max(ADDON if ctx.has["realtime"] else 0.0, native), "realtime"))
    if p["offline"] and plats:
        off = max(f["attrs"]["offline"] for f in fe_parts)
        native = max(caps.get("sync", 0), dcaps.get("sync", 0))
        add = max([ADDON * e["attrs"].get("level", 0.8) / 0.85 for e in ctx.sync
                   if db["id"] in e["attrs"].get("dbs", []) and (not e["attrs"].get("clients") or any(f["id"] in e["attrs"]["clients"] for f in fe_parts))] or [0.0])
        needs.append((1.5, math.sqrt(off * max(native, min(add, 1.0))), "offline"))
    if plats and p["project_type"] != "content_site":
        needs.append((1.5, (web_part(fe_parts)["attrs"]["app_ui"] if "web" in plats else _fe(fe_parts, lambda c: c["attrs"]["app_ui"])), "interactive UI"))
    if p["seo"]:
        needs.append((1.5, web_seo(fe_parts), "seo"))
    if p["hints"]["low_end_devices"] and plats:
        needs.append((0.7, _fe(fe_parts, lambda c: c["attrs"]["lowend"]) / 10.0, "low-end devices"))
    if p["auth"] != "none":
        needs.append((1.0, max(ADDON if ctx.has["auth"] else 0.0, caps.get("auth", 0)), "auth"))
    if p["payments"]:
        acid = min(1.0, db["attrs"].get("acid", 0.9) / 0.9)
        needs.append((1.0, (ADDON if ctx.has["payments"] else 0.0) * acid, "payments"))
    if p["multi_tenant"]:
        needs.append((1.0, dcaps.get("multi_tenant", 0.5), "multi-tenancy"))
    if p["ml"] != "none":
        needs.append((1.0, max(caps.get("ml", 0), 0.7 if ctx.has["ml"] else 0.0), "ml"))
    if p["search"]:
        needs.append((1.0, max(ADDON if ctx.has["search"] else 0.0, dcaps.get("search", 0)), "search"))
    if p["files"]:
        needs.append((1.0, max(ADDON if ctx.has["storage"] else 0.0, caps.get("storage", 0)), "files"))
    tw = sum(w for w, _, _ in needs)
    return (sum(w * c for w, c, _ in needs) / tw if tw else 1.0), needs


COLD_START = {"java": -1.0, "dotnet": -0.5}


def _pair(a, b):
    ai, bi = a["id"], b["id"]
    if b["layer"] == "hosting" and a["layer"] == "backend":
        rts = b["attrs"]["runtimes"]
        if "functions" in rts and "container" not in rts and a["attrs"]["runtime"] in COLD_START:
            return COLD_START[a["attrs"]["runtime"]]
    if bi in a.get("friction", []) or ai in b.get("friction", []):
        return -1.0
    if bi in a.get("synergy", []) or ai in b.get("synergy", []):
        return 1.0
    return 0.0


def _pair_fe(fe_parts, other):
    return _mean(_pair(f, other) for f in fe_parts)


def _db_host(db, host, mode):
    if mode == "managed_native":
        return 1.0
    if mode == "managed_external":
        return 0.5 if host["attrs"]["provider"] in db["attrs"].get("runs_on", []) else -0.3
    if mode == "self_hosted":
        return -0.3 if host["attrs"]["environment"] == "cloud" else 0.2
    return 0.0


def compat(fe_parts, be, db, host, mode):
    vals = [_pair_fe(fe_parts, be), _pair_fe(fe_parts, host), _pair(be, db), _pair(be, host), max(_pair(db, host), _db_host(db, host, mode))]
    shared = [bool(set(f.get("languages", [])) & set(be.get("languages", []))) for f in fe_parts if f.get("languages") and be.get("languages")]
    vals.append(_mean(1.0 if s else 0.0 for s in shared) if shared else 0.0)
    return _clamp(0.6 + 0.4 * _mean(vals)), vals


def skill(p, fe_parts, be, db, host):
    fe = _fe(fe_parts, lambda c: skill_fit_component(p, c))
    parts = [(0.40, fe), (0.45, skill_fit_component(p, be)), (1.0, skill_fit_component(p, db)), (1.0, skill_fit_component(p, host))]
    s = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    langs = [set(f.get("languages", [])) for f in fe_parts]
    if be.get("languages") and any(l & set(be["languages"]) for l in langs):
        s += 0.05
    return _clamp(s)


def ops_load(be, db, host, mode):
    db_eff = db["ops_burden"] + (2.5 if mode == "self_hosted" and db["id"] != "sqlite" else 0.0)
    return (0.4 * host["ops_burden"] + 0.3 * be["ops_burden"] + 0.3 * db_eff) / 10.0


def cost_range(p, ctx, fe_parts, be, db, host, mode, native_layers=()):
    """(min, max, items) monthly USD for the core stack plus reserve for non-native supporting layers."""
    items = []
    lo = hi = 0.0
    for layer, comp, m in [("hosting", host, None), ("database", db, mode), ("backend", be, None)] + [("frontend", f, None) for f in fe_parts]:
        a, b = costmod.component_cost(comp, p, m)
        items.append((layer, comp["name"], a, b))
        lo, hi = lo + a, hi + b
    return lo, hi, items


def reserve_total(ctx, needed):
    lo = hi = 0.0
    for layer, info in sorted(needed.items()):
        if info["native"]:
            continue
        a, b = ctx.reserve.get(layer, (0.0, 0.0))
        lo, hi = lo + a, hi + b
    return lo, hi


def _nines(sla):
    return -math.log10(max(1e-6, 1.0 - sla / 100.0))


def availability_level(host, p, be=None, db=None):
    """Achievable availability in 'nines' for the serving path. The HA SLA counts only when the target needs
    redundancy; on self-managed hosts (VMs, Kubernetes, on-premise) the redundant setup is the team's job, so the
    level drops with missing operations capacity. A BaaS backend caps the level at its own SLA, and an app with
    no hosted part (mobile client + BaaS) is as available as the BaaS. A managed database with a published
    SLA caps it the same way."""
    ha = p["avail_level"] >= 3.0
    a = host["attrs"]
    lvl = _nines(a["sla_ha"] if ha else a["sla"])
    if ha and host["ops_burden"] >= 4:
        lvl -= (1.0 - p["ops_capacity"]) * min(1.0, host["ops_burden"] / 5.0)
    if be is not None and be["attrs"].get("runtime") == "baas" and be["attrs"].get("sla"):
        b = _nines(be["attrs"]["sla_ha"] if ha and be["attrs"].get("sla_ha") else be["attrs"]["sla"])
        lvl = b if is_none(host) else min(lvl, b)
    if db is not None and db["attrs"].get("sla"):
        lvl = min(lvl, _nines(db["attrs"]["sla_ha"] if ha and db["attrs"].get("sla_ha") else db["attrs"]["sla"]))
    return lvl


TIMESERIES_HINTS = ("iot", "data_analytics")
# Penalty points for a database whose data model does not match the workload: (project type, db kind) -> points.
DATA_MODEL = {
    "iot": {"timeseries": 0.0, "analytics": 3.0, "document": 2.5, "keyvalue": 2.5},
    "data_analytics": {"analytics": 0.0, "timeseries": 1.5, "document": 6.0, "keyvalue": 8.0},
}


def data_model_penalty(p, db):
    """Mismatch between the project's data shape and the database's model. Relational products should not sit
    on document or key-value stores; telemetry wants a time-series store; analytics wants a columnar one; a
    specialised store (time-series) for a general workload is extra machinery. Relational stores for IoT or
    analytics are penalised more as the data volume grows (log scale, so it moves smoothly). In other product
    types, payments and tenants still add a smaller penalty to document and key-value stores."""
    kind = db["attrs"].get("kind")
    if is_none(db) or not kind:
        return 0.0
    t = p["project_type"]
    grow = _clamp(math.log10(max(p["data_gb"], 1.0) / 10.0) / 2.0)
    if t in DATA_MODEL:
        if kind == "relational":
            return (2.0 + 4.0 * grow) if t == "iot" else 3.0 * grow
        return DATA_MODEL[t].get(kind, 0.0)
    if kind == "timeseries":
        return 3.0 if t in RELATIONAL_TYPES else 4.0
    if t in RELATIONAL_TYPES and kind in ("document", "keyvalue"):
        return 8.0
    if kind in ("document", "keyvalue"):
        # payments and tenants are relational records (orders, invoices, seats, memberships) in any product type
        rel = (1.5 if p["payments"] else 0.0) + (1.0 if p["multi_tenant"] else 0.0)
        return rel * (1.5 if kind == "keyvalue" else 1.0)
    return 0.0


def budget_fit(c, budget):
    """1 well under budget, 0.7 at the budget, then a steep fall. `budget` is the reference after
    apply_budget_floor, so cost keeps separating stacks even when nothing fits the stated budget."""
    budget = max(budget, 1.0)
    return 1.0 - 0.3 * c / budget if c <= budget else 0.7 * (budget / c) ** 1.5


def _total(q, f, pen):
    return 100.0 * (0.55 * q + 0.45 * f) - pen


FLOOR_HEADROOM = 1.25
CONTENDER_POINTS = 6.0


def contender_floor(results):
    """Cheapest cost among contenders: stacks within CONTENDER_POINTS of the best score with cost ignored."""
    def cost_blind(d):
        w = d["fit_weights"]
        f = sum(w[k] * (1.0 if k == "budget" else d["fit"][k]) for k in w) / sum(w.values())
        return _total(d["Q"], f, d["penalty"])

    blind = {id(r): cost_blind(r["detail"]) for r in results}
    top = max(blind.values())
    return min(r["detail"]["cost_mid"] for r in results if blind[id(r)] >= top - CONTENDER_POINTS)


def budget_reference(budget, floor):
    """The amount budget fit is measured against: the budget, or the contender floor plus headroom when even
    the cheapest contender does not fit."""
    return budget if floor <= budget else max(budget, FLOOR_HEADROOM * floor)


def apply_budget_floor(results, p):
    """When even the cheapest contender costs more than the budget, measure budget fit against that cheapest
    cost (plus 25% headroom, so near-cheapest stacks are not punished for small differences) instead. A
    contender is a stack within CONTENDER_POINTS of the best score with cost ignored, so a cheap but poorly
    fitting stack (say a document store for billing records) does not set the yardstick for the rest.
    Without this, every stack scores ~0 on budget fit and a $0 budget would ignore cost entirely, picking
    dearer stacks than a $1,000 budget does."""
    if not results:
        return None
    floor = contender_floor(results)
    if floor <= p["budget"]:
        return None
    ref = budget_reference(p["budget"], floor)
    for r in results:
        d = r["detail"]
        d["fit"]["budget"] = budget_fit(d["cost_mid"], ref)
        w = d["fit_weights"]
        d["F"] = sum(w[k] * d["fit"][k] for k in w) / sum(w.values())
        d["score"] = _total(d["Q"], d["F"], d["penalty"])
    return ref


def score_stack(p, ctx, fe_parts, be, db, host, mode):
    """Score one complete stack. Returns a dict with score and a full breakdown."""
    q, per = quality(p, fe_parts, be, db, host)
    needed = ctx.sup.needed(p, be, db, host, mode)
    lo, hi, items = cost_range(p, ctx, fe_parts, be, db, host, mode)
    rlo, rhi = reserve_total(ctx, needed)
    lo, hi = lo + rlo, hi + rhi
    cov, needs = coverage(p, ctx, fe_parts, be, db, host)
    comp, cvals = compat(fe_parts, be, db, host, mode)
    d = ops_load(be, db, host, mode)
    ops_fit = _clamp(1.0 - 0.3 * d - 2.0 * max(0.0, d - p["ops_capacity"]))
    c = 0.35 * lo + 0.65 * hi
    bfit = budget_fit(c, p["budget"])
    def fitness(c_):
        return 1.0 if p["project_type"] in c_["strong_types"] else 0.1 if p["project_type"] in c_["weak_types"] else 0.6

    arch = _mean([_fe(fe_parts, fitness)] + [fitness(c_) for c_ in (be, db, host)])
    terms = {"skill": skill(p, fe_parts, be, db, host), "capability": cov, "archetype": arch, "ops": ops_fit,
             "budget": bfit, "compat": comp}
    w = dict(FIT_WEIGHTS)
    w["budget"] *= STRICTNESS[p["budget_strictness"]]
    f = sum(w[k] * terms[k] for k in w) / sum(w.values())

    pen = {}
    cx = 0.30 * be["complexity"] + 0.30 * host["complexity"] + 0.20 * (db["complexity"] + (1.0 if mode == "self_hosted" else 0.0)) \
        + 0.20 * (max(x["complexity"] for x in fe_parts) + (1.5 if len(fe_parts) > 1 else 0.0))
    pen["over_engineering"] = 4.0 * max(0.0, cx - p["allowed_complexity"])
    scal = [host["scores"]["scalability"]["score"]]
    if not is_none(be):
        scal.append(be["scores"]["scalability"]["score"])
    if not is_none(db):
        scal.append(min(db["scores"]["scalability"]["score"], db["attrs"].get("ceiling", 10.0)))
    pen["under_provision"] = 5.0 * max(0.0, p["scale_need"] - min(scal))
    core = [be, db, host] + list(fe_parts)
    ll = [x["lock_in"] for x in core]
    L = 0.5 * max(ll) + 0.5 * _mean(ll)
    pen["lock_in"] = LOCK[p["lock_in_tolerance"]] * 2.0 * max(0.0, L - 2.0)
    pen["hiring"] = HIRING[p["hiring_plan"]] * 0.8 * _mean([_fe(fe_parts, lambda c: max(0.0, 6.0 - c["talent_pool"])), max(0.0, 6.0 - be["talent_pool"])])
    pen["maturity"] = 0.5 * (_fe(fe_parts, lambda c: max(0.0, 7.5 - c["maturity"])) + sum(max(0.0, 7.5 - x["maturity"]) for x in (be, db, host)))
    avail = availability_level(host, p, be, db)
    gap = p["avail_level"] - avail
    pen["availability"] = 12.0 * max(0.0, gap)
    soft = 0.0
    for cert in SOFT_CERTS:
        if cert in p["compliance"]:
            for x in (host, db):
                if is_none(x) or (x["attrs"].get("environment") == "onprem") or (x.get("self_hostable") and mode == "self_hosted" and x is db):
                    continue
                if cert not in x["certs"]:
                    soft += 1.5
    pen["soft_certs"] = soft
    seo = web_seo(fe_parts)
    cold = _pair(be, host) if _pair(be, host) < 0 and be["attrs"]["runtime"] in COLD_START else 0.0
    pen["cold_start"] = -8.0 * cold * (0.4 + p["latency_need"])
    pen["seo"] = 20.0 * max(0.0, 0.6 - seo) if p["seo"] else 0.0
    pen["game_client"] = 12.0 if p["project_type"] == "gaming" and not any(f["attrs"].get("game_client") for f in fe_parts) else 0.0
    pen["data_model"] = data_model_penalty(p, db)
    # Money, health and tenant records need point-in-time recovery and failover, which an embedded single-node store lacks.
    pen["durability"] = 4.0 if db["attrs"].get("single_node") and p["project_type"] in RELATIONAL_TYPES else 0.0
    pen["game_server"] = 8.0 if p["project_type"] == "gaming" and not be["attrs"].get("game_server") else 0.0
    pen["two_codebases"] = (3.0 + 6.0 * p["time_pressure"]) if len(fe_parts) > 1 else 0.0
    bonus = 0.0
    for i in ctx.integr:
        for layer, comps in i["boosts"].items():
            ids = [x["id"] for x in ([be] if layer == "backend" else [db] if layer == "database" else [host] if layer == "hosting" else list(fe_parts) if layer == "frontend" else [])]
            if any(x in comps for x in ids):
                bonus += i["bonus"]
    pen["existing_reuse"] = -min(12.0, bonus)
    total_pen = sum(pen.values())
    score = _total(q, f, total_pen)
    return {"score": score, "Q": q, "F": f, "penalty": total_pen, "penalties": pen, "criteria": per, "fit": terms,
            "fit_weights": w, "needs": [(n_, round(c_, 3)) for _, c_, n_ in needs], "cost": (lo, hi), "cost_items": items,
            "reserve": (rlo, rhi), "ops_load": d, "complexity": cx, "compat_values": cvals, "mode": mode,
            "needed": needed, "availability_level": avail, "cost_mid": c}
