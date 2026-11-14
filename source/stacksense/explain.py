"""Plain-language explanations built from the same numbers the scorer used: justification and driven_by per
component, alternatives with a comparative why_not, tradeoffs, supporting-service rationale and the trace."""
from . import cost as costmod
from . import kb
from .constraints import is_none, region_ok, stack_reasons
from .derive import reuse_source, skill_fit_component
from .kb import CRITERIA

CRIT_LABEL = {"cost": "cost", "time_to_market": "time to market", "scalability": "scalability", "performance": "performance",
              "maintainability": "maintainability", "security": "security"}
LABEL = {"skill": "team skill fit", "capability": "coverage of the required features", "archetype": "fit to this kind of project",
         "ops": "operations load", "budget": "budget fit", "compat": "compatibility with the rest of the stack",
         "over_engineering": "unneeded complexity", "under_provision": "headroom for the expected load", "lock_in": "vendor lock-in",
         "hiring": "hiring pool", "maturity": "maturity", "availability": "the availability target", "soft_certs": "compliance attestations",
         "seo": "SEO strength", "two_codebases": "needing two codebases", "cold_start": "cold-start latency",
         "existing_reuse": "reuse of existing systems", "game_client": "a game engine client", "game_server": "a purpose-built game server", "data_model": "data-model fit", "durability": "backups and failover for business records", "mobile_preference": "the stated mobile preference", "self_run_db": "self-running a business database at a 99.9%+ target"}
for _k, _v in CRIT_LABEL.items():
    LABEL[_k] = _v
REGION_TEXT = {"india": "Indian cloud regions", "eu": "EU regions", "us": "US regions"}
KEEP_TEXT = {"india": "to keep data in India", "eu": "to keep data in the EU", "us": "to keep data in the US"}
TRIAL_LAYERS = ("frontend", "backend", "database", "hosting")
PROVIDER_REGIONS = {
    "aws": {"india": "Mumbai (ap-south-1)", "eu": "Frankfurt (eu-central-1)", "us": "N. Virginia (us-east-1)"},
    "gcp": {"india": "Mumbai (asia-south1)", "eu": "Belgium (europe-west1)", "us": "Iowa (us-central1)"},
    "azure": {"india": "Central India (Pune)", "eu": "West Europe (Netherlands)", "us": "East US (Virginia)"},
    "digitalocean": {"india": "Bangalore (BLR1)", "eu": "Frankfurt (FRA1)", "us": "New York (NYC3)"},
    "fly": {"india": "Mumbai (bom)", "eu": "Frankfurt (fra)", "us": "Ashburn (iad)"},
    "vercel": {"india": "Mumbai (bom1)", "eu": "Frankfurt (fra1)", "us": "Washington, D.C. (iad1)"},
    "hetzner": {"eu": "Falkenstein or Nuremberg (Germany)", "us": "Ashburn (Virginia)"},
    "render": {"eu": "Frankfurt", "us": "Virginia"},
    "railway": {"eu": "EU West (Amsterdam)", "us": "US East (Virginia)"},
}
VENDOR_REGIONS = {"supabase_postgres": "aws", "supabase": "aws", "firestore": "gcp", "firebase": "gcp",
                  "dynamodb": "aws", "cosmos_db": "azure"}


def target_region(p):
    """(region key, suggested) for placement: the stated residency, or the EU when GDPR applies and none is set."""
    if p["residency"] in ("india", "eu", "us"):
        return p["residency"], False
    if "gdpr" in p["compliance"] and p["residency"] == "none":
        return "eu", True
    return None, False


def region_name(comp, host, p):
    """Concrete region for a hosted component, or None when the vendor has no single named region for it."""
    key, _ = target_region(p)
    if not key or is_none(comp):
        return None
    provider = VENDOR_REGIONS.get(comp["id"])
    if provider is None:
        runs_on = comp["attrs"].get("runs_on")
        hp = host["attrs"].get("provider") if host and not is_none(host) else None
        if comp["layer"] == "hosting":
            provider = comp["attrs"].get("provider")
        elif runs_on:
            provider = hp if hp in runs_on else runs_on[0]
        else:
            provider = hp
    return PROVIDER_REGIONS.get(provider, {}).get(key)


NONE_TEXT = {
    "none_fe": ("No client application is built because the project is API-only.", ["project.platforms"]),
    "none_be": ("Pages are pre-rendered and the project needs no accounts or server-side logic, so no backend is run.",
                ["project.type", "requirements.auth", "requirements.payments"]),
    "none_db": ("There is no user data to persist; content lives in the repository.", ["project.type", "requirements.auth"]),
    "none_host": ("The backend platform runs everything server-side and the app is mobile-only, so there is nothing to host separately.",
                  ["project.platforms", "team.has_devops"]),
}


def names(parts):
    return " + ".join(c["name"] for c in parts)


def choice_name(comp_or_parts):
    parts = comp_or_parts if isinstance(comp_or_parts, list) else [comp_or_parts]
    if len(parts) == 1 and is_none(parts[0]):
        return "None"
    return names(parts)


def contributions(d, p):
    """Points each criterion, fit term and penalty contributed to the total score."""
    out = {}
    for k in CRITERIA:
        out[k] = 55.0 * p["weights"][k] * d["criteria"][k] / 10.0
    tw = sum(d["fit_weights"].values())
    for k, v in d["fit"].items():
        out[k] = 45.0 * d["fit_weights"][k] / tw * v
    for k, v in d["penalties"].items():
        out[k] = -v
    return out


def _lc(s):
    s = s.strip().rstrip(".")
    words = s.split(" ")
    if not s or not words[0][1:].islower() or (len(words) > 1 and words[1][:1].isupper() and not words[1].isupper()):
        return s
    return s[:1].lower() + s[1:]


def _dollars(lo, hi):
    return costmod.line("", lo, hi).lstrip(": ").replace(" about", "about")


def _layer_part(r, layer):
    return r["fe"] if layer == "frontend" else [r[{"backend": "be", "database": "db", "hosting": "host"}[layer]]]


def best_part(r, layer):
    return _layer_part(r, layer)


def _layer_key(r, layer):
    return tuple(c["id"] for c in _layer_part(r, layer))


def why_not(best, alt, p, layer, name):
    """Comparative reason an alternative stack scores below the best one, from the largest point gaps."""
    a, b = contributions(best["detail"], p), contributions(alt["detail"], p)
    gaps = sorted(((a[k] - b[k], k) for k in a if a[k] - b[k] > 0.4), key=lambda t: (-t[0], t[1]))
    diff = best["detail"]["score"] - alt["detail"]["score"]
    if not gaps:
        return "%s is a close second: only %.1f points behind, with no single weakness." % (name, diff)
    if diff < 0.1:
        return "%s is effectively tied (%.2f points behind); the lead comes from %s." % (name, diff, LABEL[gaps[0][1]])
    parts = ["%s (%.1f points)" % (LABEL[k], g) for g, k in gaps[:2]]
    text = "%s scores %.1f points lower overall, mainly on %s." % (name, diff, " and ".join(parts))
    lo1, hi1 = best["detail"]["cost"]
    lo2, hi2 = alt["detail"]["cost"]
    if gaps[0][1] in ("budget", "cost"):
        text += " Estimated infrastructure %s versus %s." % (_dollars(lo2, hi2), _dollars(lo1, hi1))
    return text


def _desktop_first(c, p):
    pq = c["attrs"].get("platform_quality") or {}
    return layer_is_fe(c) and pq.get("desktop", 0) >= max(pq.values(), default=0) and "desktop" not in p["client_platforms"]


def layer_is_fe(c):
    return c.get("layer") == "frontend"


def alternatives(best, ranked, info, p, layer):
    mine = _layer_key(best, layer)
    seen, out = {mine}, []
    for r in ranked:
        k = _layer_key(r, layer)
        if k in seen or (not is_none(best_part(best, layer)[0]) and any(is_none(c) for c in _layer_part(r, layer))) \
                or any(_desktop_first(c, p) for c in _layer_part(r, layer)):
            continue
        seen.add(k)
        out.append({"choice": names(_layer_part(r, layer)), "why_not": why_not(best, r, p, layer, names(_layer_part(r, layer)))})
        if len(out) == 2:
            break
    skip = {i for k in seen for i in k}
    if len(out) < 2:
        cur = {"frontend": best["fe"], "backend": best["be"], "database": best["db"], "hosting": best["host"]}
        for c in kb.core(layer):
            if c["id"] in skip or _desktop_first(c, p) or any(c["id"] == o["choice"] for o in out) or (is_none(c) and not is_none(best_part(best, layer)[0])):
                continue
            fe, be, db, host = best["fe"], best["be"], best["db"], best["host"]
            if layer == "frontend":
                fe = [c]
            elif layer == "backend":
                be = c
            elif layer == "database":
                db = c
            else:
                host = c
            rs = stack_reasons(fe, be, db, host, p)
            if rs and not any(e2["id"] == c["id"] for e2 in info["layers"][layer]["eliminated"]):
                out.append({"choice": c["name"], "why_not": "Incompatible with the rest of the stack: %s." % rs[0]})
                if len(out) >= 3:
                    break
        if not out:
            out.append({"choice": "No other eligible option", "why_not": "Every other %s option fails a hard constraint for this scenario." % layer})
    return out


def _skill_text(p, comp):
    langs = [l for l in comp.get("languages", []) if l in p["skills"]]
    if langs:
        return "matches the team's %s skills" % "/".join(langs), ["team.languages"]
    if comp.get("languages") and skill_fit_component(p, comp) >= 0.7:
        return "builds on the team's existing language skills", ["team.languages"]
    return None


def _integration_text(p, comp, layer):
    for i in kb.load()["integrations"]:
        src = reuse_source(i, p)
        if src and comp["id"] in i["boosts"].get(layer, []):
            if src == "system":
                return "reuses your existing %s" % i["label"], ["infrastructure.existing_systems"]
            return "builds on the %s licences or credits you already have" % i["label"], ["extra.existing_licenses"]
    return None


def reasons(layer, parts, r, p, d):
    """Ordered (text, fields) reasons a component or frontend pair suits this profile."""
    comp = parts[0]
    out = []
    t = _skill_text(p, comp) if layer in ("frontend", "backend") else None
    if t:
        out.append(t)
    for c in parts:
        t = _integration_text(p, c, layer)
        if t:
            out.append(t)
    a = comp["attrs"]
    if layer == "frontend":
        plats = p["client_platforms"]
        if len(parts) > 1:
            out.append(("%s serves the web and %s the mobile app, so each platform gets a first-class client" % (
                max(parts, key=lambda c: c["attrs"]["platform_quality"].get("web", 0))["name"],
                min(parts, key=lambda c: c["attrs"]["platform_quality"].get("web", 0))["name"]), ["project.platforms"]))
        elif len(plats) > 1:
            out.append(("covers %s from one codebase" % " and ".join(plats), ["project.platforms"]))
        if p["seo"] and max(c["attrs"]["seo"] for c in parts) >= 8:
            out.append(("renders pages ahead of time or on the server, which search engines can index", ["requirements.seo_critical"]))
        if p["offline"] and max(c["attrs"]["offline"] for c in parts) >= 0.8:
            out.append(("has strong offline-first support on the client", ["requirements.offline_support"]))
        if p["hints"]["low_end_devices"] and a["lowend"] >= 7:
            out.append(("keeps the footprint small for low-end phones", ["project.description"]))
    elif layer == "backend":
        if a["runtime"] == "baas":
            out.append(("is a managed backend, so there are no servers to run", ["team.has_devops", "team.size"]))
        if p["realtime"] and comp["capabilities"].get("realtime", 0) >= 0.8:
            out.append(("handles many concurrent realtime connections well", ["requirements.realtime"]))
        if p["ml"] != "none" and comp["capabilities"].get("ml", 0) >= 0.8:
            out.append(("sits next to the Python ML ecosystem", ["requirements.ml_features"]))
        if p["payments"] and comp["scores"]["security"]["score"] >= 9:
            out.append(("has mature security defaults for handling payments", ["requirements.payments"]))
    elif layer == "database":
        if p["payments"] and a.get("acid", 0) >= 0.9:
            out.append(("gives ACID transactions for money-handling data", ["requirements.payments"]))
        key, suggested = target_region(p)
        where = region_name(comp, r["host"], p) if key else None
        if key and not is_none(comp) and region_ok(comp["regions"], key):
            place = ("can be placed in %s" % where) if where else ("is available in %s" % REGION_TEXT[key])
            if suggested:
                out.append(("%s, an EU region, which keeps GDPR transfers simple" % place, ["compliance"]))
            else:
                out.append((place + (" %s" % KEEP_TEXT[key] if where else ""), ["infrastructure.data_residency"]))
        hard = [c for c in ("hipaa", "pci_dss") if c in p["compliance"] and c in comp["certs"]]
        if hard:
            out.append(("carries %s attestation" % "/".join(h.upper().replace("_", " ") for h in hard), ["compliance"]))
        if r["mode"] == "self_hosted":
            if p["deployment"] == "managed_cloud":
                out.append(("runs beside the application on %s, with no separate database bill" % r["host"]["name"], ["budget.monthly_infra_usd"]))
            else:
                out.append(("runs on your own host, as the deployment model requires", ["infrastructure.deployment_model"]))
        if a.get("ceiling", 10) >= p["scale_need"] + 1 and p["scale_need"] >= 5:
            out.append(("has headroom for the expected load", ["scale.peak_concurrent_users"]))
    elif layer == "hosting":
        if p["cloud_pref"] in ("aws", "gcp", "azure") and a["provider"] == p["cloud_pref"]:
            out.append(("runs on %s as preferred" % p["cloud_pref"].upper(), ["infrastructure.cloud_preference"]))
        key, suggested = target_region(p)
        if key and not a["static_only"] and region_ok(comp["regions"], key):
            where = region_name(comp, comp, p)
            place = ("can be placed in %s" % where) if where else ("has %s" % REGION_TEXT[key])
            if suggested:
                out.append(("%s, an EU region, which keeps GDPR transfers simple" % place, ["compliance"]))
            else:
                out.append((place + (" %s" % KEEP_TEXT[key] if where else ""), ["infrastructure.data_residency"]))
        if p["deployment"] != "managed_cloud":
            out.append(("fits the %s deployment model" % p["deployment"].replace("_", "-"), ["infrastructure.deployment_model"]))
        if a["static_only"] and p["static_candidate"]:
            out.append(("serves pre-built pages from a CDN at little or no cost", ["project.type", "budget.monthly_infra_usd"]))
        if p["avail_level"] >= 3:
            if a.get("sla_contract") is False:
                out.append(("has no contractual SLA, though about %s%% uptime is typical; accept that for the %s%% target or choose a host with an SLA" % (a["sla_ha"], p["availability"]), ["requirements.availability"]))
            else:
                out.append(("offers a %s%% SLA, in line with the %s%% target" % (a["sla_ha"], p["availability"]), ["requirements.availability"]))
    hasdb = layer == "database"
    keys = sorted(CRITERIA, key=lambda k: (-p["weights"][k] * comp["scores"][k]["score"], k))
    for k in keys[:1]:
        out.append((_lc(comp["scores"][k]["why"]), ["priorities.%s" % k]))
    if p["project_type"] in comp["strong_types"] and not is_none(comp):
        out.append(("is a proven choice for %s projects" % p["project_type"].replace("_", " "), ["project.type"]))
    return out


def justification(layer, parts, r, p, d):
    comp = parts[0]
    if len(parts) == 1 and comp["id"] in NONE_TEXT:
        text, fields = NONE_TEXT[comp["id"]]
        return text, fields
    rs = reasons(layer, parts, r, p, d)[:4]
    texts = [t for t, _ in rs]
    fields = []
    for _, fl in rs:
        for f in fl:
            if f not in fields:
                fields.append(f)
    head = "%s %s" % (names(parts), texts[0]) if texts else names(parts)
    tail = "; ".join(texts[1:])
    return head + ("; " + tail if tail else "") + ".", fields


def con_ok(text, p):
    """Keep a KB con only when it applies to this scenario."""
    if "2 GB" in text and not p["hints"]["low_end_devices"]:
        return False
    if ("Indian" in text or "India" in text) and not p["india_ctx"]:
        return False
    return True


def tradeoffs(layer, parts, r, p, d):
    out = []
    for c in parts:
        if is_none(c):
            continue
        out.extend([t for t in c.get("cons", []) if con_ok(t, p)][:2])
        if c["lock_in"] >= 6:
            out.append("High vendor lock-in: %s is hard to leave once the data model and SDKs are in use." % c["name"])
        if c["ops_burden"] >= 6:
            out.append("Needs real operations effort (patching, scaling, monitoring).")
        sk = skill_fit_component(p, c) if c.get("languages") else 1.0
        if sk < 0.5:
            out.append("New to the team (%s); plan for learning time." % "/".join(c["languages"]))
    if layer == "database" and r["mode"] == "self_hosted" and not is_none(r["db"]) and r["db"]["id"] != "sqlite":
        out.append("You run, patch and back up this database yourself on %s." % r["host"]["name"])
    if layer == "frontend" and len(parts) > 1:
        out.append("Two client codebases to build and maintain.")
    if layer == "hosting":
        lo, hi = d["cost"]
        if round(hi, -1 if hi < 1000 else -2) > p["budget"] > 0:
            out.append("Upper cost estimate (%s) exceeds the %s budget at peak." % (_dollars(lo, hi).split("about ")[-1], costmod.fmt_money(p["budget"])))
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:4]


def component(layer, best, ranked, info, p):
    parts = _layer_part(best, layer)
    d = best["detail"]
    text, fields = justification(layer, parts, best, p, d)
    name = choice_name(parts)
    if layer == "hosting" and is_none(parts[0]) and not is_none(best["be"]):
        plat = best["be"]["name"].split(" (")[0]
        reg = region_name(best["be"], None, p)
        name = "None (runs on %s)" % plat
        text = "The app is mobile-only and everything server-side runs on %s's managed platform%s, so there is nothing to host separately; its plan and region are the hosting decision." % (
            plat, " in %s" % reg if reg else "")
    tr = tradeoffs(layer, parts, best, p, d)
    if name.startswith("None (runs on"):
        tr = tr + ["Uptime, regions and resource limits follow %s's plan; moving off it later means re-hosting the backend." % plat]
    return {"choice": name, "justification": text, "driven_by": fields,
            "alternatives": alternatives(best, ranked, info, p, layer), "tradeoffs": tr}


def supporting_component(item, p, why_needed, drivers):
    e = item["entry"]
    lo, hi = costmod.evaluate(e["cost"], p)
    reasons_ = [why_needed, e["why"].rstrip(".")]
    integ = _integration_text(p, e, item["layer"])
    if integ:
        reasons_.append(integ[0])
    if item["native"]:
        reasons_.append("already part of the chosen core stack")
    elif hi < 1:
        reasons_.append("free at this scale")
    text = "%s: %s." % (e["name"], "; ".join(_lc(x) if i else x for i, x in enumerate(reasons_)))
    swaps = {x["id"]: x for x in item.get("budget_swaps", [])}
    if swaps:
        first = item["budget_swaps"][0]
        text += " Chosen over %s, which fits slightly better but costs about %s more per month." % (first["name"], costmod.fmt_money(first["saving"]))
    alts = []
    for a in item["alternatives"][:2]:
        o = kb.get(a["id"])
        if a.get("external"):
            olo, ohi = costmod.evaluate(o["cost"], p)
            alts.append({"choice": o["name"], "why_not": "A separate %s service (about %s per month%s) when %s already covers this inside the core stack; worth it once the built-in option hits its limits. Score %.2f vs %.2f."
                         % (item["layer"], _dollars(olo, ohi).split("about ")[-1].split("/month")[0], ", one more system to run" if o["ops_burden"] >= 3 else "",
                            e["name"], a["score"], item["score"])})
            continue
        if a["id"] in swaps:
            alts.append({"choice": o["name"], "why_not": "Better fit (score %.2f vs %.2f), but about %s per month more expensive; replaced to bring the plan toward the %s budget."
                         % (a["score"], item["score"], costmod.fmt_money(swaps[a["id"]]["saving"]), costmod.fmt_money(p["budget"]))})
            continue
        why = []
        if o["quality"] < e["quality"]:
            why.append("lower quality rating (%s vs %s)" % (o["quality"], e["quality"]))
        olo, ohi = costmod.evaluate(o["cost"], p)
        if ohi > hi * 1.3 + 5:
            why.append("costs more (about %s vs %s per month at the high end)" % (costmod.fmt_money(ohi), costmod.fmt_money(hi)))
        if o["ops_burden"] > e["ops_burden"]:
            why.append("more to operate")
        if o["lock_in"] > e["lock_in"] + 1:
            why.append("more lock-in")
        if a["score"] > item["score"]:
            head = "Scores higher in isolation but is %s" % ("more expensive at this budget" if olo > lo else "a weaker fit once the rest of the plan is fixed")
        else:
            head = "Ranks lower (%s)" % ", ".join(why) if why else "A close second with a smaller fit to this stack"
        alts.append({"choice": o["name"], "why_not": head + "; score %.2f vs %.2f." % (a["score"], item["score"])})
    if not alts:
        alts.append({"choice": "No other eligible option", "why_not": "No other %s service satisfies the hard constraints." % item["layer"]})
    tr = []
    if e["lock_in"] >= 6:
        tr.append("Notable vendor lock-in.")
    if e["ops_burden"] >= 6:
        tr.append("Needs operating effort (self-managed).")
    tr.extend([t for t in e.get("cons", []) if con_ok(t, p)][:1])
    if hi > 0:
        tr.append("Adds about %s per month." % _dollars(lo, hi).split("about ")[-1].split("/month")[0])
    return {"layer": item["layer"], "choice": e["name"], "justification": text, "driven_by": drivers, "alternatives": alts, "tradeoffs": tr}


def top_ranking(ranked, p, n=10):
    out = []
    for i, r in enumerate(ranked[:n], 1):
        d = r["detail"]
        out.append({"rank": i, "stack": {"frontend": choice_name(r["fe"]), "backend": choice_name(r["be"]),
                                         "database": choice_name(r["db"]), "hosting": choice_name(r["host"])},
                    "score": round(d["score"], 3), "quality": round(d["Q"], 4), "fit": round(d["F"], 4),
                    "penalty": round(d["penalty"], 3), "cost_usd": [round(d["cost"][0]), round(d["cost"][1])]})
    return out


def trace(p, ranked, info, best, sup_trace, relaxed):
    layers = {}
    for layer in TRIAL_LAYERS:
        merged = {}
        for e in info["layers"][layer]["eliminated"]:
            m = merged.setdefault(e["id"], dict(e, reasons=[]))
            m["reasons"] += [x for x in e["reasons"] if x not in m["reasons"]]
        el = sorted(merged.values(), key=lambda e: e["id"])
        cand = info["layers"][layer]["candidates"]
        used = {}
        for r in ranked:
            for c in _layer_part(r, layer):
                used[c["id"]] = max(used.get(c["id"], -1e9), r["detail"]["score"])
        short = sorted(used, key=lambda i: (-round(used[i], 9), i))
        layers[layer] = {"candidates": cand, "eliminated": el, "eligible_after_stack_checks": len(used),
                         "shortlist": short[:8], "chosen": [c["id"] for c in _layer_part(best, layer)]}
    return {
        "pipeline": ["validate input", "derive profile", "filter each layer by hard constraints", "enumerate compatible stacks",
                     "score quality, fit and penalties", "rank deterministically", "select supporting services", "estimate cost", "explain"],
        "weights": {k: round(v, 4) for k, v in p["weights"].items()},
        "layer_weights": p["layer_weights"],
        "layers": layers,
        "stack_search": {"considered": info["stacks_considered"], "pruned_by_reason": info["pruned"], "scored": len(ranked),
                         "removed_by_hard_budget": info["budget_eliminated"], "frontend_pairs": info["frontend_paired"],
                         "budget_reference_usd": round(info["budget_floor"]) if info.get("budget_floor") else round(p["budget"])},
        "top_stacks": [{"rank": i + 1, "stack": [choice_name(r["fe"]), r["be"]["name"], r["db"]["name"], r["host"]["name"]],
                        "score": round(r["detail"]["score"], 3), "quality": round(r["detail"]["Q"], 4), "fit": round(r["detail"]["F"], 4),
                        "penalties": {k: round(v, 3) for k, v in sorted(r["detail"]["penalties"].items()) if abs(v) > 0.0005},
                        "fit_terms": {k: round(v, 3) for k, v in sorted(r["detail"]["fit"].items())}} for i, r in enumerate(ranked[:5])],
        "supporting": sup_trace,
        "relaxed_constraints": relaxed,
        "score_formula": "score = 100 * (0.55 * Q + 0.45 * F) - penalties",
    }
