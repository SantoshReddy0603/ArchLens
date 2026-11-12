"""Recommendation pipeline: validate -> profile -> constrained search -> ranked stack -> supporting services
-> cost band -> explanation. Deterministic: no clock, randomness or network."""
from . import ENGINE_VERSION
from . import conflicts, cost as costmod, derive, explain, kb, robust, stacks, supporting, validate
from .constraints import is_none
from .derive import skill_fit_component

STYLE_TEXT = {
    "static": "Static-first site", "monolith": "Single deployable monolith", "modular_monolith": "Modular monolith",
    "serverless": "Serverless-first design", "service_oriented": "Service-oriented design",
    "microservices": "Microservices", "hybrid": "Hybrid design", "other": "Custom design",
}
ORCHESTRATED = ("aws_ecs_fargate", "aws_eks", "gke_cloud_run", "azure_container_apps", "onprem_k8s")
HYBRID_INTEGRATIONS = ("sql_server", "legacy_mainframe", "sap", "oracle")


def _round(x, nd=4):
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_round(v, nd) for v in x]
    return x


def _is_k8s(host):
    return host["id"].endswith("k8s") or host["id"] == "aws_eks"


def architecture_style(p, best):
    be, host = best["be"], best["host"]
    if is_none(be):
        return "static"
    rts = host["attrs"]["runtimes"]
    features = sum(bool(x) for x in (p["payments"], p["realtime"], p["search"], p["files"], p["multi_tenant"], p["ml"] != "none", p["offline"]))
    if p["team_size"] >= 8 and p["scale_need"] >= 7.5 and _is_k8s(host):
        return "microservices"
    for i in kb.load()["integrations"]:
        if i["id"] in HYBRID_INTEGRATIONS and any(i["_re"].search(s) for s in p["existing_systems"]) \
                and best["db"]["id"] not in i["boosts"].get("database", []):
            return "hybrid"
    if be["attrs"]["runtime"] == "baas" or ("functions" in rts and "container" not in rts and not host["attrs"]["static_only"]):
        return "serverless"
    if host["id"] in ORCHESTRATED and p["scale_need"] >= 6.5 and p["team_size"] >= 6 and features >= 4:
        return "service_oriented"
    if features >= 3 and p["team_size"] >= 3:
        return "modular_monolith"
    return "monolith"


def _approx(x):
    """Round to two significant figures with a K/M/B suffix, so estimates do not look precise."""
    x = float(x)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if x >= div:
            v = x / div
            return "%s%s" % (("%.0f" % v) if v >= 10 else ("%.1f" % v).rstrip("0").rstrip("."), suf)
    return "%d" % round(x)


def _money(x):
    return int(round(x)) if x < 100 else int(round(x, -1))


def _top_costs(p, best, items, n=2):
    """The n largest line items by midpoint, as readable text."""
    rows = [(supporting.label(layer), name, a, b) for layer, name, a, b in best["detail"]["cost_items"]]
    for it in items:
        a, b = costmod.evaluate(it["entry"]["cost"], p)
        rows.append((supporting.label(it["layer"]), it["entry"]["name"], a, b))
    rows = sorted((r for r in rows if r[3] >= 1), key=lambda r: (-(r[2] + r[3]), r[0], r[1]))[:n]
    return " and ".join("%s (%s, %s-%s)" % (lab.lower() if lab.isalpha() and not lab.isupper() else lab, name, costmod.fmt_money(a), costmod.fmt_money(b))
                        for lab, name, a, b in rows)


def cost_band(p, best, items):
    """Final monthly band from the chosen core and supporting services, with the assumptions behind it."""
    lo = hi = 0.0
    lines = []
    for layer, name, a, b in best["detail"]["cost_items"]:
        lo, hi = lo + a, hi + b
        if b >= 1:
            lines.append(costmod.line(supporting.label(layer) + " (" + name + ")", a, b))
    for it in items:
        a, b = costmod.evaluate(it["entry"]["cost"], p)
        lo, hi = lo + a, hi + b
        if b >= 1:
            lines.append(costmod.line(supporting.label(it["layer"]) + " (" + it["entry"]["name"] + ")", a, b))
    assume = ["Load estimate: peak about %s requests/second and about %s requests/month from %s users in 12 months."
              % (_approx(p["peak_rps"]), _approx(p["monthly_requests"]), _approx(p["users_12m"]))
              + (" Peak concurrency was estimated from the user count." if p["peak_estimated"] else ""),
              "Usage-priced items are shown as a band (0.75x to 1.3x of the estimate) because load figures are order-of-magnitude.",
              "Public list prices of each product, in USD, without taxes, committed-use discounts or developer time."]
    if costmod.needs_ha(p):
        assume.append("The %s%% availability target doubles or adds redundancy for hosting and database where needed." % p["availability"])
    if not lines:
        assume.append("Everything fits inside free tiers at this scale.")
    return {"min": _money(lo), "max": max(_money(hi), _money(lo)), "assumptions": assume + lines}


def _dedupe(ws):
    seen, out = set(), []
    for w in ws:
        k = (w["code"], w["message"])
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


def _base(sid):
    return {"scenario_id": sid, "engine_version": ENGINE_VERSION}


def _invalid(scenario, errors, assumptions, n):
    sid = scenario.get("scenario_id") if isinstance(scenario, dict) and isinstance(scenario.get("scenario_id"), str) else None
    out = _base(sid)
    out.update({
        "status": "invalid_input",
        "summary": "The scenario has %d invalid or missing field%s, so no recommendation was made; fix the listed fields and run again."
                   % (len(errors), "" if len(errors) == 1 else "s"),
        "architecture_style": "other", "stack": None,
        "estimated_monthly_cost_usd": {"min": 0, "max": 0, "assumptions": ["No estimate: the input is invalid."]},
        "warnings": _dedupe(n.get("_warnings", [])), "assumptions": assumptions, "validation_errors": errors,
        "decision_trace": {"pipeline": ["validate input"], "stopped_at": "validate input"}, "profile": None, "score": None, "ranking": [],
    })
    return out


def _infeasible(sid, n, p, info, warnings, assumptions, errors):
    crit = []
    if info["budget_eliminated"] and not info.get("closest"):
        pass
    for layer in kb.CORE_LAYERS:
        el = info["layers"][layer]["eliminated"]
        counts = {}
        for e in el:
            counts[e["reasons"][0]] = counts.get(e["reasons"][0], 0) + 1
        if counts and len(el) >= info["layers"][layer]["candidates"] - 1:
            top = sorted(counts.items(), key=lambda t: (-t[1], t[0]))[:2]
            crit.append({"code": "NO_FEASIBLE_STACK", "severity": "critical",
                         "message": "Every %s option is excluded: %s." % (layer, "; ".join(r for r, _ in top)),
                         "fields": ["infrastructure", "compliance"]})
    closest = info.get("closest")
    if info["budget_eliminated"]:
        msg = "Every compatible stack costs more than the hard budget of %s per month." % costmod.fmt_money(p["budget"])
        if closest:
            lo, hi = closest["detail"]["cost"]
            msg += " The cheapest option is %s on %s, about %s per month." % (
                explain.choice_name(closest["fe"]) + " with " + closest["be"]["name"] + " and " + closest["db"]["name"], closest["host"]["name"],
                costmod.fmt_money(lo) + "-" + costmod.fmt_money(hi))
        crit.append({"code": "BUDGET_INFEASIBLE", "severity": "critical", "message": msg,
                     "fields": ["budget.monthly_infra_usd", "scale.users_12_months"]})
    if not crit:
        top = sorted(info["pruned"].items(), key=lambda t: (-t[1], t[0]))[:3]
        crit.append({"code": "NO_FEASIBLE_STACK", "severity": "critical",
                     "message": "No combination of components satisfies every hard constraint together (%s)." % "; ".join(k for k, _ in top),
                     "fields": ["infrastructure", "compliance"]})
    fixes = robust.repairs(n)
    for f in fixes:
        st = f["stack"]
        crit.append({"code": "FEASIBLE_IF_CHANGED", "severity": "info",
                     "message": "If you %s, a stack becomes possible: %s with %s and %s on %s (core layers about %s-%s per month)." % (
                         f["change"], st["frontend"], st["backend"], st["database"], st["hosting"],
                         costmod.fmt_money(f["cost_usd"][0]), costmod.fmt_money(f["cost_usd"][1])),
                     "fields": [f["field"]]})
    if not fixes:
        crit.append({"code": "FEASIBLE_IF_CHANGED", "severity": "info",
                     "message": "No single change to residency, compliance, cloud, deployment model or budget strictness is enough on its own; relax two of them or revisit the platform and feature requirements.",
                     "fields": ["infrastructure", "compliance"]})
    out = _base(sid)
    out.update({
        "status": "infeasible",
        "summary": "No stack satisfies all hard constraints at once; the critical warnings name what blocks it and what to relax.",
        "architecture_style": "other", "stack": None,
        "estimated_monthly_cost_usd": {"min": 0, "max": 0, "assumptions": ["No estimate: no feasible stack."]},
        "warnings": _dedupe(warnings + crit), "assumptions": assumptions, "validation_errors": errors,
        "decision_trace": {"pipeline": ["validate input", "derive profile", "filter each layer by hard constraints", "enumerate compatible stacks"],
                           "stopped_at": "no feasible stack",
                           "layers": {l: {"candidates": info["layers"][l]["candidates"], "eliminated": info["layers"][l]["eliminated"]} for l in kb.CORE_LAYERS},
                           "stack_search": {"considered": info["stacks_considered"], "pruned_by_reason": info["pruned"],
                                            "removed_by_hard_budget": info["budget_eliminated"]}},
        "profile": _round(p), "score": None, "ranking": [], "repairs": fixes,
    })
    if closest:
        out["closest_option"] = explain.top_ranking([closest], p, 1)[0]
    return out


def _summary(p, style, best, band, relaxed_budget):
    fe, be, db, host = explain.choice_name(best["fe"]), explain.choice_name(best["be"]), explain.choice_name(best["db"]), best["host"]["name"]
    parts = [fe if fe != "None" else None, be if be != "None" else None, ("%s database" % db) if db != "None" else None]
    core = ", ".join(x for x in parts if x)
    on = "" if is_none(best["host"]) else " on %s" % host
    first = "%s: %s%s." % (STYLE_TEXT[style], core if core else "pre-rendered pages", on)
    facts = []
    if p["residency"] in ("india", "eu", "us") and not best["host"]["attrs"]["static_only"]:
        facts.append("keeps data in %s" % {"india": "Indian regions", "eu": "EU regions", "us": "US regions"}[p["residency"]])
    if p["static_candidate"]:
        facts.append("needs no server or database")
    elif any(l in p["skills"] for c in best["fe"] + [best["be"]] for l in c.get("languages", [])):
        facts.append("matches the team's languages")
    if relaxed_budget:
        facts.append("exceeds the stated budget (about %s-%s per month)" % (costmod.fmt_money(band["min"]), costmod.fmt_money(band["max"])))
    elif costmod.fmt_money(band["min"]) == costmod.fmt_money(band["max"]):
        facts.append("is estimated at %s per month" % costmod.fmt_money(band["max"]))
    else:
        facts.append("is estimated at %s-%s per month" % (costmod.fmt_money(band["min"]), costmod.fmt_money(band["max"])))
    return first + " It " + (", ".join(facts[:-1]) + (" and " if len(facts) > 1 else "") + facts[-1]) + "."


def recommend(scenario):
    """Run the full pipeline on one scenario dict and return the output dict (Section 4 schema plus extras)."""
    n, errors, assumptions = validate.validate(scenario)
    if errors:
        return _invalid(scenario, errors, assumptions, n)
    sid = scenario.get("scenario_id")
    p = derive.build_profile(n)
    assumptions = list(assumptions) + list(p["notes"])
    warnings = conflicts.detect(n)
    ranked, info = stacks.enumerate_stacks(p)
    if not ranked:
        if info["budget_eliminated"]:
            soft = dict(p, budget_strictness="firm")
            cl, _ = stacks.enumerate_stacks(soft)
            info["closest"] = min(cl, key=lambda r: (r["detail"]["cost"][0], stacks.key(r))) if cl else None
        return _infeasible(sid, n, p, info, warnings, assumptions, errors)
    best = ranked[0]
    if info.get("budget_floor"):
        p["budget_reference"] = info["budget_floor"]
        assumptions.append("No compatible stack fits the %s budget, so cost was compared against a reference of about %s per month (the cheapest compatible stack plus 25%%) to keep the cheaper options ahead."
                           % (costmod.fmt_money(p["budget"]), costmod.fmt_money(info["budget_floor"])))
    items, sup_warn, sup_assume = supporting.select(p, best["be"], best["db"], best["host"], best["mode"], best["fe"])
    band = cost_band(p, best, items)
    if p["budget"] > 0 and p["budget_strictness"] != "hard" and (band["min"] + band["max"]) / 2.0 > p["budget"]:
        core_mid = sum((a + b) / 2.0 for _, _, a, b in best["detail"]["cost_items"])
        parts = {"backend": best["be"], "hosting": best["host"], "database": best["db"], "frontend": best["fe"]}
        trial = [dict(it) for it in items]
        swaps = supporting.cheapen(p, parts, trial, core_mid, p["budget"])
        if swaps:
            tband = cost_band(p, best, trial)
            if (tband["min"] + tband["max"]) / 2.0 <= p["budget"] or (band["min"] > p["budget"] >= tband["min"]):
                items, band, assumptions = trial, tband, list(assumptions) + swaps
    relaxed = list(sup_assume)
    relaxed_budget = band["min"] > p["budget"] and p["budget_strictness"] != "hard"
    extra = []
    if relaxed_budget:
        msg = "The cheapest compliant option still costs about %s-%s per month, above the %s budget." % (
            costmod.fmt_money(band["min"]), costmod.fmt_money(band["max"]), costmod.fmt_money(p["budget"]))
        extra.append({"code": "BUDGET_EXCEEDED", "severity": "critical", "message": msg, "fields": ["budget.monthly_infra_usd"]})
        relaxed.append("Relaxed: the monthly budget of %s was exceeded because no compliant stack fits inside it." % costmod.fmt_money(p["budget"]))
    elif (band["min"] + band["max"]) / 2.0 > p["budget"] > 0:
        extra.append({"code": "BUDGET_AT_RISK", "severity": "warning",
                      "message": "Expected cost (%s-%s) has its midpoint above the %s budget; the low end fits. The largest items are %s, so start there: trim optional services, cap usage, or accept a tighter margin."
                                 % (costmod.fmt_money(band["min"]), costmod.fmt_money(band["max"]), costmod.fmt_money(p["budget"]), _top_costs(p, best, items)),
                      "fields": ["budget.monthly_infra_usd", "scale.peak_concurrent_users"]})
    elif band["max"] > p["budget"] > 0:
        extra.append({"code": "BUDGET_PEAK_HEADROOM", "severity": "info",
                      "message": "Expected cost (%s-%s) is inside the %s budget at typical load but can pass it at peak usage; watch %s first."
                                 % (costmod.fmt_money(band["min"]), costmod.fmt_money(band["max"]), costmod.fmt_money(p["budget"]), _top_costs(p, best, items)),
                      "fields": ["budget.monthly_infra_usd", "scale.peak_concurrent_users"]})
    if band["max"] <= p["budget"]:
        # The early check works from load alone; the priced stack shows the budget is enough after all.
        warnings = [w for w in warnings if w["code"] != "BUDGET_TOO_LOW_FOR_SCALE"]
    for w in warnings:
        if w["code"] == "BUDGET_TOO_LOW_FOR_SCALE":
            w["message"] = ("At this load (peak about %s requests/second, %s users in 12 months) the recommended stack is expected to cost about %s-%s per month; the budget is %s."
                            % (_approx(p["peak_rps"]), _approx(p["users_12m"]), costmod.fmt_money(band["min"]), costmod.fmt_money(band["max"]), costmod.fmt_money(p["budget"])))
            if band["min"] <= p["budget"]:
                w["severity"] = "warning"
    sk = min(skill_fit_component(p, c) for c in best["fe"] + [best["be"]] if c.get("languages")) if any(c.get("languages") for c in best["fe"] + [best["be"]]) else 1.0
    if sk < 0.5:
        extra.append({"code": "SKILL_MISMATCH", "severity": "warning",
                      "message": "The chosen stack uses languages the team does not know well; expect a learning curve (skill fit %.0f%%)." % (100 * sk),
                      "fields": ["team.languages", "team.experience"]})
    gone = {e["id"] for e in info["layers"]["database"]["eliminated"]}
    purpose = [c["id"] for c in kb.core("database") if c["attrs"].get("kind") in ("analytics", "timeseries") and c["id"] not in gone]
    if p["project_type"] in ("iot", "data_analytics") and not purpose:
        extra.append({"code": "PROJECT_TYPE_NOT_COVERED", "severity": "warning",
                      "message": "No purpose-built %s database passes the hard constraints, so a general-purpose database was chosen; it handles a pilot, but plan a time-series or columnar store (ClickHouse, TimescaleDB, BigQuery) once volumes grow."
                                 % ("time-series" if p["project_type"] == "iot" else "analytics"),
                      "fields": ["project.type"]})
    elif p["project_type"] in ("iot", "data_analytics") and best["db"]["attrs"].get("kind") not in ("analytics", "timeseries"):
        extra.append({"code": "GENERAL_PURPOSE_DATABASE", "severity": "info",
                      "message": "At about %s of data a general-purpose database (%s) is simpler and cheaper than a %s store; revisit this once data passes roughly 100 GB or queries scan months of history."
                                 % ("%d GB" % max(1, round(p["data_gb"])), best["db"]["name"], "time-series" if p["project_type"] == "iot" else "columnar analytics"),
                      "fields": ["project.type", "scale.data_volume_gb_12_months"]})
    if not p["skills"] and not any(x["code"] == "SKILL_MISMATCH" for x in extra):
        extra.append({"code": "SKILL_MISMATCH", "severity": "warning",
                      "message": "No team languages were given, so language fit could not be checked; list the languages the team knows to get a skill-aware recommendation.",
                      "fields": ["team.languages"]})
    if any(it["layer"] == "payments" and it["id"].startswith("razorpay") for it in items):
        extra.append({"code": "PCI_TOKENISATION", "severity": "info",
                      "message": "Razorpay: use Standard Checkout or hosted fields so card numbers are tokenised by Razorpay and never touch your servers; this keeps you in the lightest PCI DSS scope.",
                      "fields": ["requirements.payments"]})
    if p["budget"] < 1 and band["max"] <= 0 and not any(w["code"] == "FREE_TIER_LIMITS" for w in warnings + extra):
        hosted = [c["name"] for c in (best["host"], best["be"], best["db"])
                  if not is_none(c) and (c is not best["be"] or c["attrs"].get("runtime") == "baas")] + [it["entry"]["name"] for it in items]
        extra.append({"code": "FREE_TIER_LIMITS", "severity": "info",
                      "message": "A $0 budget relies on free plans; the stack fits them at this scale, but read the free-plan terms and limits of %s (build minutes, request quotas, commercial-use rules) before launch."
                                 % (_join(hosted) if hosted else "each provider"),
                      "fields": ["budget.monthly_infra_usd"]})
    elif p["budget"] < 1 and band["max"] > 0 and not any(w["code"] == "FREE_TIER_LIMITS" for w in warnings + extra):
        free = _free_tier_names(p, best, items)
        extra.append({"code": "FREE_TIER_LIMITS", "severity": "info",
                      "message": "A $0 budget only works on free tiers; the recommended stack stays free at typical load but can reach about %s per month at peak, so check the free-tier limits of %s."
                                 % (costmod.fmt_money(band["max"]), _join(free) if free else "each provider"),
                      "fields": ["budget.monthly_infra_usd"]})
    extra += _cert_gaps(p, best, items) + _reuse_notes(p, best, items) + _sms_cost(p, items, band)
    credit, credit_w = _credits(p, best, items, band, ranked)
    if credit:
        band["credits"] = credit
    extra += credit_w + _overengineering_note(p, best, ranked)
    style = architecture_style(p, best)
    stack = {layer: explain.component(layer, best, ranked, info, p) for layer in explain.TRIAL_LAYERS}
    stack["supporting"] = [explain.supporting_component(it, p, it.get("why") or (supporting.WHY_NEEDED[it["layer"]] % _fmt_arg(it["layer"], p) if "%s" in supporting.WHY_NEEDED[it["layer"]]
                                                       else supporting.WHY_NEEDED[it["layer"]]), supporting.DRIVERS.get(it["layer"], [])) for it in items]
    sup_trace = {"%s%s" % (it["layer"], "" if it["sub"] is None else ":" + it["sub"]): {
        "chosen": it["id"], "covered_natively": it["native"], "score": round(it["score"], 4),
        "alternatives": [a["id"] for a in it["alternatives"]], "eliminated": it["eliminated"]} for it in items}
    all_w = _dedupe(warnings + extra + sup_warn)
    status = "ok_with_warnings" if any(w["severity"] in ("warning", "critical") for w in all_w) else "ok"
    d = best["detail"]
    out = _base(sid)
    out.update({
        "status": status, "summary": _summary(p, style, best, band, relaxed_budget), "architecture_style": style, "stack": stack,
        "estimated_monthly_cost_usd": band, "warnings": all_w, "assumptions": assumptions + relaxed, "validation_errors": [],
        "decision_trace": _round(explain.trace(p, ranked, info, best, sup_trace, relaxed)),
        "profile": _round(p),
        "score": {"total": round(d["score"], 3), "quality": round(d["Q"], 4), "fit": round(d["F"], 4), "penalty": round(d["penalty"], 3),
                  "penalties": {k: round(v, 3) for k, v in sorted(d["penalties"].items())},
                  "fit_terms": {k: round(v, 4) for k, v in sorted(d["fit"].items())},
                  "criteria": {k: round(v, 3) for k, v in sorted(d["criteria"].items())}},
        "ranking": explain.top_ranking(ranked, p),
        "robustness": _round(robust.robustness(p, ranked, best)),
        "trade_off_options": _round(robust.trade_off_options(p, ranked, best)),
    })
    return out


SOFT_CERT_LABEL = {"gdpr": "GDPR", "soc2": "SOC 2", "iso27001": "ISO 27001"}


def _join(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _free_tier_names(p, best, items):
    out = []
    for layer, name, a, b in best["detail"]["cost_items"]:
        if a < 1 <= b:
            out.append(name)
    for it in items:
        a, b = costmod.evaluate(it["entry"]["cost"], p)
        if a < 1 <= b:
            out.append(it["entry"]["name"])
    return out


def _cert_gaps(p, best, items):
    """Soft certifications (GDPR, SOC 2, ISO 27001) are scored, not filtered, so name every chosen hosted
    component that publishes no attestation for them. Self-hosted parts are your own audit scope."""
    out = []
    for cert in ("gdpr", "soc2", "iso27001"):
        if cert not in p["compliance"]:
            continue
        miss = []
        baas = best["be"] if best["be"]["attrs"].get("runtime") == "baas" else None
        core = [best["host"], best["db"]] + ([baas] if baas else [])
        ids = {best["be"]["id"], best["db"]["id"], best["host"]["id"]}
        for x in core:
            if is_none(x) or x["attrs"].get("environment") == "onprem" or (x is best["db"] and best["mode"] == "self_hosted"):
                continue
            if cert not in x["certs"]:
                miss.append(x["name"])
        miss += [it["entry"]["name"] for it in items if it["entry"].get("requires_saas") and cert not in it["entry"]["certs"]
                 and not ids & set(it["entry"]["attrs"].get("bundled_with", []) + it["entry"]["attrs"].get("native_with", []))]
        miss = list(dict.fromkeys(miss))
        if miss:
            out.append({"code": "CERT_GAP", "severity": "warning",
                        "message": "%s is required, but %s list%s no %s attestation in the knowledge base; ask the vendor%s for evidence (DPA, audit report) or replace %s."
                                   % (SOFT_CERT_LABEL[cert], _join(miss), "s" if len(miss) == 1 else "", SOFT_CERT_LABEL[cert],
                                      "" if len(miss) == 1 else "s", "it" if len(miss) == 1 else "them"),
                        "fields": ["compliance"]})
    return out


def _reuse_notes(p, best, items):
    """Say for each recognised existing system whether the recommended stack reuses it or needs a bridge."""
    chosen = {"frontend": [c["id"] for c in best["fe"]], "backend": [best["be"]["id"]], "database": [best["db"]["id"]],
              "hosting": [best["host"]["id"]]}
    for it in items:
        chosen.setdefault(it["layer"], []).append(it["id"])
    out = []
    for i in kb.load()["integrations"]:
        src = derive.reuse_source(i, p)
        if not src:
            continue
        used = sorted({kb.load()["by_id"][c]["name"] for layer, ids in i["boosts"].items() for c in ids if c in chosen.get(layer, [])})
        if src == "licence":
            if used:
                out.append({"code": "LICENCE_REUSED", "severity": "info",
                            "message": "Your existing %s licences or credits are used through %s." % (i["label"], _join(used)),
                            "fields": ["extra.existing_licenses"]})
            continue
        if used:
            out.append({"code": "EXISTING_SYSTEM_REUSED", "severity": "info",
                        "message": "%s is reused through %s. %s" % (i["label"], _join(used), i["note"]),
                        "fields": ["infrastructure.existing_systems"]})
        else:
            out.append({"code": "EXISTING_SYSTEM_NOT_REUSED", "severity": "info",
                        "message": "%s is not part of the recommended stack, so plan a connector or a data migration. %s" % (i["label"], i["note"]),
                        "fields": ["infrastructure.existing_systems"]})
    return out


CLOUDS = {"aws": "AWS", "gcp": "Google Cloud", "azure": "Azure"}


def _billed_by(layer, entry, best):
    """Which hyperscaler sends the bill for one cost line, if any."""
    host = best["host"]["attrs"].get("provider")
    if layer == "hosting":
        return host
    if layer == "database":
        return host if best["detail"]["mode"] in ("managed_native", "self_hosted") else entry.get("billed_by")
    if layer in ("backend", "frontend"):
        return entry.get("billed_by")
    prov = entry.get("provider")
    return host if prov in ("any_cloud", "self") else prov


def _credit_provider(p, best):
    held = [x[:-len("_credits")] for x in p["licenses"] if x.endswith("_credits") and x[:-len("_credits")] in CLOUDS]
    host = best["host"]["attrs"].get("provider")
    if held:
        return (host if host in held else sorted(held)[0]), False
    if p.get("cloud_credits_usd") and host in CLOUDS:
        return host, True
    return None, False


def _credits(p, best, items, band, ranked):
    """How far cloud credits the team already holds stretch on the recommended stack. The band itself stays at list price."""
    prov, assumed = _credit_provider(p, best)
    amount = p.get("cloud_credits_usd")
    if not prov:
        if amount:
            return None, [{"code": "CLOUD_CREDITS", "severity": "info",
                           "message": "Credits of %s were given, but the recommended stack is not billed by AWS, Google Cloud or Azure, so they do not lower its cost."
                                      % costmod.fmt_money(amount), "fields": ["extra.cloud_credits_usd"]}]
        return None, []
    by_id = kb.load()["by_id"]
    lines = [(layer, by_id[best[{"hosting": "host", "database": "db", "backend": "be"}[layer]]["id"]] if layer != "frontend" else None, a, b)
             for layer, _, a, b in best["detail"]["cost_items"] if layer != "frontend"]
    lines += [(it["layer"], it["entry"], *costmod.evaluate(it["entry"]["cost"], p)) for it in items]
    lo = sum(a for layer, e, a, b in lines if e is not None and _billed_by(layer, e, best) == prov)
    hi = sum(b for layer, e, a, b in lines if e is not None and _billed_by(layer, e, best) == prov)
    name = CLOUDS[prov]
    if hi < 1:
        alt = next((r for r in ranked[1:] if r["host"]["attrs"].get("provider") == prov), None)
        tail = ""
        if alt is not None:
            diff = best["detail"]["score"] - alt["detail"]["score"]
            what = robust._swap_text(robust.changed(best, alt))
            if diff > 0:
                tail = " The best %s-hosted option (%s) scores %.1f points lower." % (name, what, diff)
            elif amount:
                cc = stacks.credit_cost(p) or stacks._cost_mid
                tail = (" The best %s-hosted option (%s) is in a near tie, but even with the credits spread over the first year its core layers cost more (about %s against %s per month)."
                        % (name, what, costmod.fmt_money(cc(alt)), costmod.fmt_money(cc(best))))
            else:
                tail = (" The best %s-hosted option (%s) is in a near tie and loses on price; give extra.cloud_credits_usd so the credits count in that comparison."
                        % (name, what))
        return None, [{"code": "CLOUD_CREDITS", "severity": "info",
                       "message": "Your %s credits do not apply here: the recommended stack has no %s line items.%s" % (name, name, tail),
                       "fields": ["extra.existing_licenses", "extra.cloud_credits_usd"]}]
    lo, hi = _money(lo), max(_money(hi), _money(lo))
    out = {"provider": name, "amount_usd": amount, "covered_usd": [lo, hi]}
    note = " (assumed to be %s credits, the recommended host's provider)" % name if assumed else ""
    if amount:
        mid = (lo + hi) / 2.0
        months = amount / mid if mid > 0 else None
        first = [_money(max(0.0, 12 * band["min"] - min(amount, 12 * lo)) / 12.0), _money(max(0.0, 12 * band["max"] - min(amount, 12 * hi)) / 12.0)]
        out.update({"months_covered": round(months, 1) if months is not None else None, "first_year_average_usd": first})
        span = "more than two years" if months is None or months > 24 else "about %s month%s" % (_approx(months) if months >= 2 else "%.1f" % months, "" if 0.95 <= months < 1.05 else "s")
        msg = ("Credits of %s%s cover the %s line items (about %s-%s of the %s-%s per month) for %s; averaged over the first year the bill is about %s per month. The cost band shows list prices before credits."
               % (costmod.fmt_money(amount), note, name, costmod.fmt_money(lo), costmod.fmt_money(hi), costmod.fmt_money(band["min"]),
                  costmod.fmt_money(band["max"]), span,
                  costmod.fmt_money(first[1]) if first[0] == first[1] else "%s-%s" % (costmod.fmt_money(first[0]), costmod.fmt_money(first[1]))))
    else:
        msg = ("Your %s credits apply to the %s line items, about %s-%s of the %s-%s per month; give extra.cloud_credits_usd to see how long they last."
               % (name, name, costmod.fmt_money(lo), costmod.fmt_money(hi), costmod.fmt_money(band["min"]), costmod.fmt_money(band["max"])))
    return out, [{"code": "CLOUD_CREDITS", "severity": "info", "message": msg, "fields": ["extra.existing_licenses", "extra.cloud_credits_usd"]}]


def _overengineering_note(p, best, ranked):
    """Name the closest heavier setup when most of its deficit comes from complexity and operations load for this team."""
    mine = best["detail"]
    for r in ranked[1:]:
        d = r["detail"]
        if d["complexity"] < mine["complexity"] + 1.0:
            continue
        gap = mine["score"] - d["score"]
        heavy = (d["penalties"]["over_engineering"] - mine["penalties"]["over_engineering"]) \
            + 100 * 0.45 * d["fit_weights"]["ops"] * (mine["fit"]["ops"] - d["fit"]["ops"])
        if gap < 0.5 or heavy < 1.5 or heavy < 0.5 * gap:
            continue
        return [{"code": "OVERENGINEERING_AVOIDED", "severity": "info",
                 "message": "A heavier setup (%s) was considered and is %.1f points behind; %.1f of those points come from the extra complexity and operations work it puts on a team of %d%s."
                            % ("%s with %s on %s" % (r["be"]["name"], r["db"]["name"], r["host"]["name"]), gap, min(heavy, gap), p["team_size"],
                               " with a DevOps engineer" if p["has_devops"] else " without a DevOps engineer"),
                 "fields": ["team.size", "team.has_devops", "team.experience"]}]
    return []


def _sms_cost(p, items, band):
    """One-time-password SMS is priced per message and can dominate a small budget."""
    for it in items:
        if it["layer"] != "sms":
            continue
        a, b = costmod.evaluate(it["entry"]["cost"], p)
        mid, total = (a + b) / 2.0, (band["min"] + band["max"]) / 2.0
        if mid >= 20 and (mid > 0.3 * max(p["budget"], 1.0) or mid > 0.4 * total):
            return [{"code": "SMS_COST", "severity": "warning",
                     "message": "SMS one-time passwords through %s cost about %s-%s per month, a large share of the budget; email magic links, passkeys or WhatsApp OTP cut this, and SMS can stay as a fallback."
                                % (it["entry"]["name"], costmod.fmt_money(a), costmod.fmt_money(b)),
                     "fields": ["requirements.auth", "budget.monthly_infra_usd"]}]
    return []


def _fmt_arg(layer, p):
    return {"auth": p["auth"].replace("_", " "), "ml": p["ml"].replace("_", " ")}.get(layer, "")
