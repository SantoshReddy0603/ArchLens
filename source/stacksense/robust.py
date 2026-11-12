"""How firm the recommendation is, and what each direction of change would cost.

Both parts re-score the stacks the search already scored, so they cost no extra search and stay exact:
changing a criterion weight only changes Q, and changing the budget only changes the budget fit term.

- robustness: does the same stack still win when one priority counts twice or half as much, or when the
  budget is halved or doubled? Uses the same winner rule as the ranking (near ties go to the cheaper stack).
- trade_off_options: for common requests ("cheaper", "less to operate", "more headroom", ...) the
  best-scoring stack that actually delivers it, how many points it gives up and where those points go.
"""
from . import cost as costmod
from .explain import CRIT_LABEL, LABEL, choice_name
from .kb import CRITERIA
from .scoring import budget_fit, budget_reference, contender_floor
from .stacks import TIE_POINTS, credit_cost, key, winner

FACTORS = ((2.0, "counted twice as much"), (0.5, "counted half as much"))
LAYERS = ("frontend", "backend", "database", "hosting")
OPTION_WINDOW = 12.0


def _parts(r):
    return {"frontend": r["fe"], "backend": [r["be"]], "database": [r["db"]], "hosting": [r["host"]]}


def _names(r):
    return {l: choice_name(r["fe"] if l == "frontend" else r[{"backend": "be", "database": "db", "hosting": "host"}[l]])
            for l in LAYERS}


def changed(best, alt):
    """Layers whose component differs, as (layer, from, to)."""
    a, b = _parts(best), _parts(alt)
    out = []
    for l in LAYERS:
        if [c["id"] for c in a[l]] != [c["id"] for c in b[l]]:
            out.append((l, choice_name(a[l] if l == "frontend" else a[l][0]), choice_name(b[l] if l == "frontend" else b[l][0])))
    return out


def _cap(s):
    return s[:1].upper() + s[1:]


def _swap_text(ch):
    return "; ".join("%s %s -> %s" % (l, x, y) for l, x, y in ch)


def _weights(w, k, f):
    raw = dict(w)
    raw[k] = raw[k] * f
    t = sum(raw.values())
    return {j: raw[j] / t for j in raw}


def _q(cv, w):
    return sum(wj * c for wj, c in zip(w, cv)) / 10.0


def _score(q, f, pen):
    return 100.0 * (0.55 * q + 0.45 * f) - pen


def _f_with_budget(d, bfit):
    w = d["fit_weights"]
    tot = sum(w.values())
    return d["F"] + w["budget"] * (bfit - d["fit"]["budget"]) / tot


def robustness(p, ranked, best):
    """Re-run the winner rule under single changes to the weights and the budget."""
    checks = []
    base = p["weights"]
    cvec = {id(r): tuple(r["detail"]["criteria"][j] for j in CRITERIA) for r in ranked}
    for k in CRITERIA:
        for f, how in FACTORS:
            w2 = _weights(base, k, f)
            # The gap between two stacks can move by at most 55 * (total weight change) points, so stacks further
            # behind than that (plus the near-tie window) cannot win and are skipped.
            bound = 55.0 * sum(abs(w2[j] - base[j]) for j in CRITERIA) + TIE_POINTS + 0.5
            top = best["detail"]["score"]
            pool = [r for r in ranked if r["detail"]["score"] >= top - bound]
            wv = tuple(w2[j] for j in CRITERIA)
            sc = {id(r): _score(_q(cvec[id(r)], wv), r["detail"]["F"], r["detail"]["penalty"]) for r in pool}
            win = winner(pool, lambda r: sc[id(r)], credit_cost(p))
            checks.append({"change": "%s %s" % (CRIT_LABEL[k], how), "kind": "weight", "criterion": k, "factor": f,
                           "same_stack": key(win) == key(best), "_win": win})
    budget = p["budget"]
    if budget >= 1:
        floor = contender_floor(ranked)
        for f, how in ((0.5, "budget halved"), (2.0, "budget doubled")):
            nb = budget * f
            if p["budget_strictness"] == "hard" and f > 1:
                continue
            pool = [r for r in ranked if not (p["budget_strictness"] == "hard" and r["detail"]["cost"][0] > nb)]
            if not pool:
                checks.append({"change": "%s (%s)" % (how, costmod.fmt_money(nb)), "kind": "budget", "factor": f,
                               "same_stack": False, "_win": None})
                continue
            ref = budget_reference(nb, floor)
            sc = {id(r): _score(r["detail"]["Q"], _f_with_budget(r["detail"], budget_fit(r["detail"]["cost_mid"], ref)), r["detail"]["penalty"])
                  for r in pool}
            win = winner(pool, lambda r: sc[id(r)], credit_cost(p))
            checks.append({"change": "%s (%s)" % (how, costmod.fmt_money(nb)), "kind": "budget", "factor": f,
                           "same_stack": key(win) == key(best), "_win": win})
    flips = [c for c in checks if not c["same_stack"]]
    runner = next((r for r in ranked if key(r) != key(best)), None)
    margin = best["detail"]["score"] - runner["detail"]["score"] if runner else None
    verdict = "stable" if not flips else "mostly stable" if len(flips) <= 2 else "sensitive"
    out_checks = []
    for c in checks:
        item = {"change": c["change"], "same_stack": c["same_stack"]}
        if not c["same_stack"]:
            if c["_win"] is None:
                item["result"] = "no stack fits"
            else:
                ch = changed(best, c["_win"])
                item["new_stack"] = _names(c["_win"])
                item["swaps"] = [{"layer": l, "from": x, "to": y} for l, x, y in ch]
        out_checks.append(item)
    held = len(checks) - len(flips)
    if not flips:
        summary = "The same stack wins in all %d checks: each priority counted twice or half as much%s." % (
            len(checks), ", and the budget halved or doubled" if any(c["kind"] == "budget" for c in checks) else "")
    else:
        groups = {}
        for c in flips:
            out = "nothing (no stack fits)" if c["_win"] is None else ", ".join("%s (%s)" % (y, l) for l, x, y in changed(best, c["_win"]))
            groups.setdefault(out, []).append(c["change"])
        parts = ["%s when %s" % (out, ", ".join(ch)) for out, ch in groups.items()]
        summary = "The same stack wins in %d of %d checks. It switches to %s." % (held, len(checks), "; and to ".join(parts))
    if margin is not None and margin < 0:
        summary += (" The runner-up scores %.2f points higher but costs more, and stacks within %.2f points count as a tie that"
                    " the cheaper one wins." % (-margin, TIE_POINTS))
    return {"verdict": verdict, "margin_points": None if margin is None else round(margin, 3),
            "runner_up": _names(runner) if runner else None, "checks_run": len(checks), "checks_held": held,
            "summary": summary, "checks": out_checks}


def _lockin(r):
    ll = [c["lock_in"] for c in r["fe"] + [r["be"], r["db"], r["host"]]]
    return 0.5 * max(ll) + 0.5 * sum(ll) / len(ll)


def _point_moves(p, best, alt):
    """Where the score difference comes from, in points, per criterion, fit term and penalty."""
    db, da = best["detail"], alt["detail"]
    moves = {}
    for k in CRITERIA:
        moves[k] = 55.0 * p["weights"][k] * (da["criteria"][k] - db["criteria"][k]) / 10.0
    w = db["fit_weights"]
    tot = sum(w.values())
    for k in sorted(w):
        moves[k] = 45.0 * w[k] * (da["fit"][k] - db["fit"][k]) / tot
    for k in sorted(set(db["penalties"]) | set(da["penalties"])):
        moves[k] = moves.get(k, 0.0) - (da["penalties"].get(k, 0.0) - db["penalties"].get(k, 0.0))
    return moves


def _dir_defs(p, best):
    d = best["detail"]
    mid = d["cost_mid"]
    defs = []
    if mid >= 10:
        defs.append(("cheaper", "Spend less", ("budget", "cost"),
                     lambda r: r["detail"]["cost_mid"] <= 0.8 * mid,
                     lambda r: "about %d%% cheaper on the core layers (%s against %s per month)" % (
                         round(100 * (1 - r["detail"]["cost_mid"] / mid)), _band(r), _band(best))))
    if d["ops_load"] >= 0.75:
        defs.append(("less_ops", "Less to operate", ("ops",),
                     lambda r: r["detail"]["ops_load"] <= d["ops_load"] - 0.75,
                     lambda r: "operations load %.1f instead of %.1f (0-10 scale)" % (r["detail"]["ops_load"], d["ops_load"])))
    defs.append(("more_headroom", "More headroom to scale", ("scalability", "under_provision"),
                 lambda r: r["detail"]["criteria"]["scalability"] >= d["criteria"]["scalability"] + 0.75,
                 lambda r: "scalability %.1f instead of %.1f" % (r["detail"]["criteria"]["scalability"], d["criteria"]["scalability"])))
    defs.append(("faster_build", "Ship sooner", ("time_to_market",),
                 lambda r: r["detail"]["criteria"]["time_to_market"] >= d["criteria"]["time_to_market"] + 0.75,
                 lambda r: "time to market %.1f instead of %.1f" % (r["detail"]["criteria"]["time_to_market"], d["criteria"]["time_to_market"])))
    if d["fit"]["skill"] <= 0.85:
        defs.append(("team_skills", "Closer to the team's skills", ("skill",),
                     lambda r: r["detail"]["fit"]["skill"] >= d["fit"]["skill"] + 0.15,
                     lambda r: "team skill fit %d%% instead of %d%%" % (round(100 * r["detail"]["fit"]["skill"]), round(100 * d["fit"]["skill"]))))
    lb = _lockin(best)
    if lb > 3.0:
        defs.append(("less_lock_in", "Less vendor lock-in", ("lock_in",),
                     lambda r: _lockin(r) <= lb - 1.5,
                     lambda r: "lock-in %.1f instead of %.1f (0-10 scale)" % (_lockin(r), lb)))
    return defs


def _band(r):
    lo, hi = r["detail"]["cost"]
    return "%s-%s" % (costmod.fmt_money(lo), costmod.fmt_money(hi))


def trade_off_options(p, ranked, best):
    """For each direction, the highest-ranked stack within OPTION_WINDOW points that delivers it."""
    top = best["detail"]["score"]
    pool = [r for r in ranked if key(r) != key(best) and r["detail"]["score"] >= top - OPTION_WINDOW]
    out = []
    for did, title, own, test, gain in _dir_defs(p, best):
        alt = next((r for r in pool if test(r)), None)
        if alt is None:
            out.append({"direction": did, "title": title, "available": False,
                        "note": "Nothing within %d points of the winner does clearly better here, so the chosen stack is already a strong pick on this." % OPTION_WINDOW})
            continue
        moves = _point_moves(p, best, alt)
        losses = sorted(((v, k) for k, v in moves.items() if v < -0.05 and k not in own), key=lambda t: (t[0], t[1]))[:3]
        gives = ["%s (%.1f pts)" % (LABEL.get(k, k.replace("_", " ")), v) for v, k in losses]
        ch = changed(best, alt)
        out.append({"direction": did, "title": title, "available": True, "stack": _names(alt),
                    "swaps": [{"layer": l, "from": x, "to": y} for l, x, y in ch],
                    "score": round(alt["detail"]["score"], 3), "points_behind": round(top - alt["detail"]["score"], 3),
                    "cost_usd": [round(alt["detail"]["cost"][0]), round(alt["detail"]["cost"][1])],
                    "gains": gain(alt), "gives_up": gives,
                    "summary": "%s: %s. Gives %s %.1f points%s." % (
                        _cap(_swap_text(ch)), gain(alt), "up" if top >= alt["detail"]["score"] else "back",
                        abs(top - alt["detail"]["score"]), ", mainly on " + ", ".join(gives) if gives else "")})
    return out


def _relaxations(n):
    """Single input changes worth trying when no stack is feasible, most specific first."""
    from .constraints import CLOUDS, HARD_CERTS
    infra, extra = n["infrastructure"], n.get("extra") or {}
    out = []
    res = infra.get("data_residency")
    if res and res != "none":
        out.append(("infrastructure.data_residency", "allow data to be stored outside %s" % {"india": "India", "eu": "the EU", "us": "the US"}.get(res, res),
                    lambda m: m["infrastructure"].__setitem__("data_residency", "none")))
    for c in HARD_CERTS:
        if c in n.get("compliance", []):
            out.append(("compliance", "accept providers without a signed %s attestation (only if that is legally acceptable)" % c.upper().replace("_", " "),
                        lambda m, c=c: m.__setitem__("compliance", [x for x in m["compliance"] if x != c])))
    if infra.get("cloud_preference") in CLOUDS + ("none",):
        out.append(("infrastructure.cloud_preference", "allow any cloud provider instead of %s" % infra["cloud_preference"],
                    lambda m: m["infrastructure"].__setitem__("cloud_preference", "any")))
    if infra.get("deployment_model") in ("on_premise", "air_gapped", "self_hosted_cloud_vm"):
        out.append(("infrastructure.deployment_model", "use managed cloud services instead of %s" % infra["deployment_model"].replace("_", "-"),
                    lambda m: m["infrastructure"].__setitem__("deployment_model", "managed_cloud")))
    if extra.get("budget_strictness") == "hard":
        out.append(("extra.budget_strictness", "treat the budget as firm rather than hard",
                    lambda m: m["extra"].__setitem__("budget_strictness", "firm")))
    return out


def repairs(n):
    """For an infeasible scenario, which single change makes a stack possible, and what it would be.
    Each entry re-runs the full constrained search on the changed input, so it is as exact as the main run."""
    import copy
    from . import derive, stacks
    out = []
    for field, text, apply in _relaxations(n):
        m = copy.deepcopy(n)
        apply(m)
        ranked, _ = stacks.enumerate_stacks(derive.build_profile(m))
        if not ranked:
            continue
        best = ranked[0]
        out.append({"field": field, "change": text, "stack": _names(best),
                    "cost_usd": [round(best["detail"]["cost"][0]), round(best["detail"]["cost"][1])],
                    "score": round(best["detail"]["score"], 3)})
    return out
