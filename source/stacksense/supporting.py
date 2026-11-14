"""Supporting services: which layers a project needs, which options survive the hard constraints, and the
best pick given the chosen core stack. Same filter-then-score approach as the core layers."""
import math

from . import cost as costmod
from . import kb
from .constraints import CLOUDS, HARD_CERTS, region_ok
from .derive import reuse_source

LAYER_LABEL = {"ci_cd": "CI/CD", "sms": "SMS", "cdn": "CDN", "waf": "WAF", "dns": "DNS", "ml": "ML", "ingest": "Device ingestion",
               "sync": "Offline sync", "forms": "Forms", "auth": "Auth", "geo": "Geospatial"}


def label(layer):
    return LAYER_LABEL.get(layer, layer.replace("_", " ").capitalize())


NO_RESIDENCY_LAYERS = ("cdn", "dns", "ci_cd", "waf")
GEO_DBS = ("postgresql", "neon", "supabase_postgres", "timescaledb")
NATIVE_CDN_PROVIDERS = ("cloudflare", "netlify", "vercel", "github")
NATIVE_CDN_HOSTS = ("aws_amplify", "azure_static_web_apps", "firebase_hosting")
LAYER_ORDER = ["auth", "sms", "payments", "storage", "search", "geo", "realtime", "sync", "cache", "queue", "ingest", "ml", "cdn",
               "waf", "forms", "email", "notifications", "analytics", "dns", "ci_cd", "observability"]
DRIVERS = {
    "auth": ["requirements.auth"], "sms": ["requirements.auth"],
    "payments": ["requirements.payments", "infrastructure.data_residency", "compliance"],
    "storage": ["requirements.file_media_storage", "scale.data_volume_gb_12_months"],
    "search": ["requirements.full_text_search", "scale.users_12_months"],
    "geo": ["project.description"],
    "realtime": ["requirements.realtime", "scale.peak_concurrent_users"],
    "sync": ["requirements.offline_support", "project.platforms"],
    "cache": ["scale.peak_concurrent_users", "requirements.p95_latency_ms", "scale.workload"],
    "queue": ["scale.peak_concurrent_users", "requirements.payments", "requirements.file_media_storage"],
    "ingest": ["project.type", "scale.peak_concurrent_users"], "ml": ["requirements.ml_features"], "cdn": ["scale.users_12_months", "project.platforms"],
    "waf": ["compliance", "requirements.payments"], "forms": ["project.type", "requirements.auth"],
    "email": ["requirements.auth", "requirements.payments"], "notifications": ["project.platforms"],
    "analytics": ["project.type"], "dns": ["infrastructure.deployment_model"], "ci_cd": ["team.size"],
    "observability": ["requirements.availability", "scale.users_12_months"],
}
WHY_NEEDED = {
    "auth": "users sign in (%s)", "sms": "phone one-time passwords need SMS delivery", "payments": "the product takes payments",
    "storage": "users upload files", "search": "full-text search is required",
    "geo": "the description mentions GPS or location data, which needs spatial types and indexes", "realtime": "live updates are required",
    "sync": "the app must work offline and reconcile later", "cache": "load and latency targets call for a cache",
    "queue": "background work (payments, media, ML or burst absorption) should leave the request path",
    "ingest": "devices send telemetry that needs a purpose-built MQTT ingestion layer", "ml": "ML features are required (%s)", "cdn": "assets are delivered to a wide audience", "waf": "regulated payment/health traffic needs a web application firewall",
    "forms": "a content site needs a way to receive messages", "email": "transactional email (verification, receipts, invites)",
    "notifications": "mobile users expect push notifications", "analytics": "a public site benefits from basic traffic analytics",
    "dns": "a self-run host needs managed DNS", "ci_cd": "automated build and deploy for the team",
    "observability": "production monitoring for the availability target",
}


def _self_contained(e):
    return e.get("self_hostable") and not e.get("requires_saas")


def _own_host(p):
    return p["deployment"] in ("on_premise", "air_gapped") or p["cloud_pref"] == "none"


def _self_run(e, p):
    """True when the service runs on your own host (so provider regions and attestations do not apply)."""
    return e["provider"] == "self" or (e.get("self_hostable") and _own_host(p))


def _persistent(host):
    return host["attrs"]["environment"] in ("vm", "onprem") or bool({"container", "vm"} & set(host["attrs"]["runtimes"]))


def reasons(e, p, be=None, host=None):
    """Hard-constraint reasons a supporting service cannot be used."""
    r = []
    layer, dep, pref, res = e["layer"], p["deployment"], p["cloud_pref"], p["residency"]
    if dep in ("on_premise", "air_gapped") and not _self_contained(e):
        r.append("%s deployment forbids SaaS dependencies" % dep.replace("_", "-"))
    if pref == "none" and not _self_contained(e):
        r.append("cloud_preference none needs self-managed services")
    if pref in CLOUDS and e["provider"] in CLOUDS and e["provider"] != pref:
        r.append("cloud_preference is %s and this is a %s service" % (pref, e["provider"]))
    selfrun = _self_run(e, p)
    if layer not in NO_RESIDENCY_LAYERS and not selfrun and not region_ok(e["regions"], res):
        r.append("no %s region available" % res)
    if not selfrun:
        miss = [c for c in HARD_CERTS if c in p["compliance"] and c not in e["certs"]
                and (c == "pci_dss" and layer in ("payments", "storage", "cache", "queue", "search", "auth") or c == "hipaa" and layer not in NO_RESIDENCY_LAYERS + ("payments",))]
        if miss:
            r.append("no %s attestation" % "/".join(m.upper() for m in miss))
    if host is not None and e["provider"] == "self" and not e["attrs"].get("db_native") and layer in ("realtime", "queue", "cache", "search", "ingest") and not _persistent(host):
        r.append("needs a long-running process, which %s does not provide" % host["name"])
    if e["attrs"].get("workforce") and (p["auth"] != "enterprise_sso" or layer == "email") and not any(
            pat in s_.lower() for pat in e["attrs"].get("integrates", []) for s_ in p["existing_systems"]):
        r.append("sends through the organisation's Microsoft 365 tenant, which is not among existing_systems" if layer == "email"
                 else "workforce single sign-on is for employees, not consumer sign-in")
    if layer in ("observability", "auth") and e["provider"] == "self" and dep not in ("on_premise", "air_gapped") and not p["has_devops"]:
        r.append("self-hosted %s needs a team that runs infrastructure" % ("monitoring" if layer == "observability" else "identity"))
    langs = e["attrs"].get("langs")
    if be is not None and langs and be["languages"] and not set(langs) & set(be["languages"][:1]):
        r.append("works with %s backends, not %s" % ("/".join(langs), be["name"]))
    return r


def feasible(layer, p, be=None, host=None):
    return [e for e in kb.supporting(layer) if not reasons(e, p, be, host)]


def _r2(x):
    """Round to two significant figures so load figures are not stated with false precision."""
    if x < 100:
        return "%d" % round(x)
    mag = 10 ** (len(str(int(x))) - 2)
    v = int(round(x / mag) * mag)
    return "{:,}".format(v)


def skip_notes(p, need):
    """Short notes for load-driven layers that are not needed, so the omission is explicit."""
    out = []
    if "cache" not in need:
        out.append("No cache layer: about %s requests/second and %s users in 12 months are served directly by the database or BaaS." % (_r2(p["peak_rps"]), _r2(p["users_12m"])))
    if "queue" not in need and (p["payments"] or p["files"]):
        out.append("No queue: background work is light enough for the backend or the provider's own webhooks and functions.")
    return out


def needed(p, be, db, host, mode):
    """Layers needed by this project mapped to {native: bool}; native means the core stack already covers it."""
    caps = be["capabilities"]
    out = {}
    plat = p["client_platforms"]
    scale = p["scale_need"]
    if p["auth"] != "none":
        nat = be["attrs"]["runtime"] == "baas" or (caps.get("auth", 0) >= 0.8 and p["auth"] in ("email_password", "social"))
        out["auth"] = {"native": nat}
        if p["auth"] == "phone_otp":
            out["sms"] = {"native": be["id"] == "firebase"}
    if p["payments"]:
        out["payments"] = {"native": False}
        if "pci_dss" in p["compliance"] or (p["project_type"] in ("fintech", "ecommerce") and p["peak_rps"] >= 50):
            out["waf"] = {"native": False, "why": "card traffic under %s needs a web application firewall" % ("PCI DSS" if "pci_dss" in p["compliance"] else "a %s product at this load" % p["project_type"])}
    if p["files"] or p["data_gb"] > 200:
        out["storage"] = {"native": be["attrs"]["runtime"] == "baas"}
    if p["search"]:
        fts = kb.get("postgres_fts")
        nat = bool(fts) and db["id"] in fts["attrs"].get("db_native", []) and p["data_gb"] < 200 and p["peak_concurrent"] < 5000
        out["search"] = {"native": nat}
    if p["realtime"]:
        crdt = p["project_type"] == "realtime_collab"
        host_rt = 0.0 if host["attrs"]["static_only"] else (0.3 if "functions" in host["attrs"]["runtimes"] and "container" not in host["attrs"]["runtimes"] else 1.0)
        if be["attrs"]["runtime"] == "baas":
            host_rt = 1.0
        nat = min(max(caps.get("realtime", 0), db["capabilities"].get("realtime", 0)), host_rt) >= 0.85 and not crdt
        out["realtime"] = {"native": nat}
    if p["offline"] and plat:
        out["sync"] = {"native": False}
    rps, users = p["peak_rps"], p["users_12m"]
    why_cache = None
    if rps >= 200:
        why_cache = "peak load of about %s requests/second is better served from a cache than from the database" % _r2(rps)
    elif p["p95"] is not None and p["p95"] <= 200 and rps >= 50:
        why_cache = "the %d ms p95 target at about %s requests/second needs hot reads served from memory" % (p["p95"], _r2(rps))
    elif users >= 100000:
        why_cache = "%s users in 12 months make repeated reads worth caching" % _r2(users)
    elif p["realtime"] and p["peak_concurrent"] >= 2000:
        why_cache = "%s concurrent users on a live feature need fast shared state" % _r2(p["peak_concurrent"])
    if why_cache and be["id"] != "none_be":
        out["cache"] = {"native": db["id"] == "none_db" or be["attrs"]["runtime"] == "baas", "why": why_cache}
    no_server = be["id"] == "none_be"
    why_queue = None
    if p["ml"] in ("llm_rag", "training"):
        why_queue = "ML work (%s) is slow and should run outside the request path" % p["ml"].replace("_", " ")
    elif p["payments"] and rps >= 100:
        why_queue = "payment callbacks and receipts at about %s requests/second should be processed asynchronously" % _r2(rps)
    elif p["project_type"] in ("fintech", "healthtech") and rps >= 50:
        why_queue = "KYC, ledger and notification jobs in a %s product should run asynchronously" % p["project_type"]
    elif p["files"] and rps >= 300:
        why_queue = "media processing at about %s requests/second should leave the request path" % _r2(rps)
    elif rps >= 500:
        why_queue = "bursts at about %s requests/second need buffering" % _r2(rps)
    if why_queue and not no_server:
        out["queue"] = {"native": caps.get("jobs", 0) >= 0.8 or be["attrs"]["runtime"] == "baas", "why": why_queue}
    if p["ml"] != "none":
        out["ml"] = {"native": False}
    if p["project_type"] == "iot":
        out["ingest"] = {"native": False}
    has_cdn = host["attrs"]["static_only"] or host["attrs"]["provider"] in NATIVE_CDN_PROVIDERS or host["id"] in NATIVE_CDN_HOSTS
    if "web" in plat and not has_cdn and p["project_type"] != "internal_tool" and p["users_12m"] >= 5000 and (p["egress_gb"] > 200 or p["geography"] in ("global", "multi_region")) and p["deployment"] == "managed_cloud":
        out["cdn"] = {"native": False}
    if p["static_candidate"]:
        out["forms"] = {"native": False}
        out["analytics"] = {"native": False}
    if p["auth"] == "email_password":
        out["email"] = {"native": False, "why": "email and password sign-in needs verification and reset emails"}
    elif p["multi_tenant"]:
        out["email"] = {"native": False, "why": "multi-tenant workspaces send invite emails"}
    elif p["payments"] and p["auth"] not in ("phone_otp", "none"):
        out["email"] = {"native": False, "why": "payments need emailed receipts and confirmations"}
    elif p["hints"].get("approvals") and p["auth"] != "none":
        out["email"] = {"native": False, "why": "the approval steps in the description notify requesters and approvers", "drivers": ["project.description", "requirements.auth"]}
    if p["hints"].get("location") and db["id"] in GEO_DBS:
        out["geo"] = {"native": True}
    if ("android" in plat or "ios" in plat) and p["project_type"] in ("social", "marketplace", "fintech", "ecommerce", "healthtech", "realtime_collab", "web_app", "saas_b2b", "gaming"):
        out["notifications"] = {"native": be["id"] == "firebase"}
    if host["attrs"]["environment"] in ("vm", "onprem") and p["deployment"] != "air_gapped" or p["static_candidate"]:
        out["dns"] = {"native": False, "why": "a self-run host needs managed DNS" if host["attrs"]["environment"] in ("vm", "onprem")
                      else "a custom domain on static hosting needs DNS records"}
    if not p["static_candidate"] or p["team_size"] > 1:
        out["ci_cd"] = {"native": False}
    if not p["static_candidate"]:
        out["observability"] = {"native": False}
    return out


def _cost_mid(e, p):
    lo, hi = costmod.evaluate(e["cost"], p)
    return lo, hi


def reserve_costs(p):
    """Cheapest feasible service cost per layer (used to account for supporting spend while ranking stacks)."""
    out = {}
    for layer in LAYER_ORDER:
        subs = _ml_subs(p) if layer == "ml" else [None]
        lo_t = hi_t = 0.0
        for sub in subs:
            best = None
            for e in feasible(layer, p):
                if sub is not None and e["attrs"].get("sub") != sub:
                    continue
                lo, hi = _cost_mid(e, p)
                if best is None or (lo + hi, e["id"]) < (best[0] + best[1], best[2]):
                    best = (lo, hi, e["id"])
            if best:
                lo_t, hi_t = lo_t + best[0], hi_t + best[1]
        out[layer] = (lo_t, hi_t)
    return out


SOFT_CERTS = ("soc2", "iso27001", "gdpr")


def _score(e, p, parts, layer):
    be, host, db = parts["backend"], parts["hosting"], parts["database"]
    lo, hi = _cost_mid(e, p)
    mid = (lo + hi) / 2
    budget = max(p.get("budget_reference") or p["budget"], 1.0)
    cost_fit = 1.0 / (1.0 + mid / (0.08 * budget))
    aff = 0.5
    if e["provider"] == host["attrs"]["provider"] and e["provider"] in CLOUDS:
        aff += 0.4
    bundled = e["attrs"].get("bundled_with", []) + e["attrs"].get("native_with", [])
    ids = [be["id"], host["id"], db["id"]] + [f["id"] for f in parts["frontend"]]
    if any(b in ids for b in bundled):
        aff += 0.5
    for pat in e["attrs"].get("integrates", []):
        if any(pat in s.lower() for s in p["existing_systems"]):
            aff += 0.5
            break
    if e["attrs"].get("pairs_with") and be["id"] in e["attrs"]["pairs_with"]:
        aff += 0.25
    if e["attrs"].get("db_native") and db["id"] in e["attrs"]["db_native"]:
        aff += 0.35
    if e["attrs"].get("clients") and not any(f["id"] in e["attrs"]["clients"] for f in parts["frontend"]):
        aff -= 0.4
    if e["attrs"].get("frontends") and not any(f["id"] in e["attrs"]["frontends"] for f in parts["frontend"]):
        aff -= 0.15
    for i in kb.load()["integrations"]:
        if e["id"] in i["boosts"].get(layer, []) and reuse_source(i, p):
            aff += 0.6
            break
    hp = next((x["attrs"].get("provider") for x in (host, be) if x["attrs"].get("provider") in CLOUDS), host["attrs"]["provider"])
    if hp in CLOUDS and layer in ("auth", "storage", "queue", "cache", "realtime", "search", "observability", "ml", "ingest", "cdn"):
        if e["provider"] in CLOUDS and e["provider"] != hp:
            aff -= 0.45
        elif e["provider"] == "self" and layer in ("storage", "queue", "cache"):
            aff -= 0.15
        elif e["provider"] not in CLOUDS and e["provider"] not in ("self", "any_cloud") and layer in ("storage", "queue", "cache"):
            aff -= 0.2
    elif hp not in CLOUDS and e["provider"] in CLOUDS and layer in ("auth", "storage", "queue", "cache", "realtime", "ingest") and p["cloud_pref"] != e["provider"]:
        aff -= 0.5
    if layer in ("realtime", "search") and e["provider"] == "self" and p["has_devops"] and p["ops_capacity"] >= 0.6 and e["ops_burden"] <= 4:
        aff += 0.2
    aff = max(0.0, min(1.0, aff))
    s = 0.40 * e["quality"] / 10 + 0.18 * cost_fit + 0.12 * (1 - e["ops_burden"] / 10) + 0.10 * (1 - e["lock_in"] / 10) + 0.20 * aff
    ms = e["attrs"].get("min_scale")
    if ms:
        s -= 0.06 * max(0.0, ms - p["scale_need"])
    if layer == "auth":
        m = p["auth"]
        cov = e["attrs"].get(m, 1.0 if m in e["attrs"].get("modes", []) else 0.0)
        if m in ("email_password", "social"):
            cov = 1.0 if m in e["attrs"].get("modes", []) else 0.3
        s *= 0.45 + 0.55 * cov
        if be["capabilities"].get("auth", 0) >= 0.8 and e["id"] == "framework_auth" and m in ("email_password", "social"):
            s += 0.12
    if layer == "payments":
        if p["hints"]["upi"] and not e["attrs"].get("upi"):
            s *= 0.8
        if p["india_ctx"] and "india" in e["attrs"].get("countries", []):
            s *= 1.12
    if layer == "sms" and p["india_ctx"] and "india" in e["attrs"].get("countries", []):
        s *= 1.12
    if layer == "realtime" and e["attrs"].get("collab"):
        s *= 1.2 if p["project_type"] == "realtime_collab" else 0.85
    if layer == "ml" and e["attrs"].get("sub") == "llm" and p["project_type"] in ("fintech", "healthtech"):
        s += 0.2 if e["attrs"].get("private_model") else 0.0
    if e["attrs"].get("heavy_ci") and p["team_size"] < 10:
        s -= 0.15
    if layer == "search" and e["id"] == "postgres_fts":
        s += 0.04 if p["peak_concurrent"] < 5000 else -0.2
    if e.get("requires_saas"):
        s -= 0.12 * sum(1 for c in SOFT_CERTS if c in p["compliance"] and c not in e["certs"])
    return s


def _ml_subs(p):
    return {"inference_api": ["inference" if p["hints"]["tabular_ml"] else "llm"], "llm_rag": ["llm", "vector"], "training": ["training"]}.get(p["ml"], [])


def select(p, be, db, host, mode, fe_parts):
    """Pick supporting services for the chosen core stack. Returns (items, warnings, assumptions)."""
    parts = {"backend": be, "database": db, "hosting": host, "frontend": fe_parts}
    need = needed(p, be, db, host, mode)
    items, warnings, assumptions = [], [], skip_notes(p, need)
    ids_core = {be["id"], host["id"], db["id"]} | {f["id"] for f in fe_parts}
    chosen = set()
    for layer in LAYER_ORDER:
        if layer not in need:
            continue
        subs = _ml_subs(p) if layer == "ml" else [None]
        for sub in subs:
            cands = [e for e in kb.supporting(layer) if (sub is None or e["attrs"].get("sub") == sub)]
            ok, elim = [], []
            for e in cands:
                rs = reasons(e, p, be if layer == "queue" else None, host)
                if layer == "sync":
                    cl, dbs = e["attrs"].get("clients", []), e["attrs"].get("dbs", [])
                    if db["id"] not in dbs:
                        rs = rs + ["does not sync with %s" % db["name"]]
                    if cl and not any(f["id"] in cl for f in fe_parts):
                        rs = rs + ["no SDK for %s" % " + ".join(f["name"] for f in fe_parts)]
                dbn = e["attrs"].get("db_native")
                if dbn and db["id"] not in dbn:
                    rs = rs + ["needs PostgreSQL"]
                bw = e["attrs"].get("bundled_with")
                if bw and not set(bw) & (ids_core | chosen):
                    rs = rs + ["is part of %s only" % "/".join(kb.get(b)["name"] if kb.get(b) else b for b in bw)]
                if layer == "payments" and p["residency"] == "india" and "india" not in e["attrs"].get("countries", []) and "global" not in e["attrs"].get("countries", []):
                    rs = rs + ["not available for India"]
                (elim if rs else ok).append((e, rs))
            lname = layer if sub is None else "%s (%s)" % (layer, sub)
            all_ok = list(ok)
            if need[layer]["native"]:
                ids = {be["id"], host["id"], db["id"]} | {f["id"] for f in fe_parts}
                ok = [(e, rs) for e, rs in ok if ids & set(e["attrs"].get("bundled_with", [])) or db["id"] in e["attrs"].get("db_native", [])
                      or e["attrs"].get("native") or (e["id"] == "framework_auth" and be["capabilities"].get("auth", 0) >= 0.8 and p["auth"] in ("email_password", "social"))
                      or ids & set(e["attrs"].get("native_with", []))]
                if not ok:
                    need[layer] = dict(need[layer], native=False)
                    ok = [x for x in all_ok]
            if not ok:
                if need[layer]["native"] or layer in ("forms", "analytics", "dns"):
                    continue
                code = "AIRGAPPED_SAAS_REQUIREMENT" if p["deployment"] in ("air_gapped", "on_premise") and layer in ("payments", "sms") else "NO_COMPLIANT_SERVICE"
                why = "; ".join(sorted({r for _, rs in elim for r in rs[:1]})[:3])
                if code == "AIRGAPPED_SAAS_REQUIREMENT":
                    msg = "%s needs an external provider, which %s deployment forbids (%s). The recommendation omits it; reach it through a controlled gateway or choose a different deployment model." % (label(layer), p["deployment"].replace("_", "-"), why)
                    assumptions.append("Relaxed: %s was left out of the stack because every available provider is a SaaS dependency." % layer)
                else:
                    msg = "No %s option satisfies the hard constraints (%s). The layer is left out of the recommendation." % (lname, why)
                    assumptions.append("Relaxed: no compliant %s service was found, so the layer is left unfilled." % lname)
                warnings.append({"code": code, "severity": "critical", "message": msg, "fields": DRIVERS.get(layer, [])[:2]})
                continue
            scored = sorted(((-round(_score(e, p, parts, layer), 9), e["id"], e) for e, _ in ok))
            best = scored[0][2]
            score_of = {s[1]: -s[0] for s in scored}
            item = {"layer": layer if sub is None else layer, "id": best["id"], "entry": best, "sub": sub,
                    "native": need[layer]["native"], "why": need[layer].get("why"), "drivers": need[layer].get("drivers"), "score": score_of[best["id"]],
                    "alternatives": [], "pool": [e for _, _, e in scored], "eliminated": [{"id": e["id"], "name": e["name"], "reasons": rs} for e, rs in elim]}
            for _, i, e in scored[1:3]:
                item["alternatives"].append({"id": i, "name": e["name"], "score": score_of[i]})
            if need[layer]["native"] and len(item["alternatives"]) < 2:
                ext = sorted((-round(_score(e, p, parts, layer), 9), e["id"], e) for e, _ in all_ok if e["id"] not in score_of)
                for neg, i, e in ext[:2 - len(item["alternatives"])]:
                    item["alternatives"].append({"id": i, "name": e["name"], "score": -neg, "external": True})
            items.append(item)
            chosen.add(best["id"])
    return items, warnings, assumptions


def _mid(e, p):
    lo, hi = costmod.evaluate(e["cost"], p)
    return (lo + hi) / 2.0


def _low(e, p):
    return costmod.evaluate(e["cost"], p)[0]


def cheapen(p, parts, items, core_mid, limit, low_end=False):
    """Swap usage-priced supporting services for cheaper compliant ones, one at a time, until the stack's
    cost midpoint (or, for a hard budget, its low end) fits the limit or nothing cheaper is left.
    Returns notes describing each swap."""
    notes = []
    _mid_ = _low if low_end else _mid
    for _ in range(12):
        costs = [_mid_(it["entry"], p) for it in items]
        if core_mid + sum(costs) <= limit:
            break
        best = None
        for i, it in enumerate(items):
            if it.get("native"):
                continue
            cur = costs[i]
            cur_s = _score(it["entry"], p, parts, it["layer"])
            for e in it["pool"]:
                gain = cur - _mid_(e, p)
                if gain < max(5.0, 0.1 * cur):
                    continue
                loss = max(0.0, cur_s - _score(e, p, parts, it["layer"])) + 0.01
                k = (-gain / loss, e["id"])
                if best is None or k < best[0]:
                    best = (k, i, e, gain)
        if best is None:
            break
        _, i, e, gain = best
        it = items[i]
        prev = it["entry"]
        old = prev["name"]
        pool = list({x["id"]: x for x in it["pool"] + [prev]}.values())
        it["entry"], it["id"], it["pool"] = e, e["id"], pool
        it["score"] = _score(e, p, parts, it["layer"])
        it.setdefault("budget_swaps", []).append({"id": prev["id"], "name": old, "score": _score(prev, p, parts, it["layer"]), "saving": gain})
        rest = [x for x in pool if x["id"] != e["id"]]
        it["alternatives"] = [{"id": x["id"], "name": x["name"], "score": _score(x, p, parts, it["layer"])} for x in sorted(rest, key=lambda x: (-_score(x, p, parts, it["layer"]), x["id"]))[:2]]
        notes.append("%s: %s replaced by %s to bring the stack toward the budget (about %s per month cheaper%s)." % (
            label(it["layer"]), old, e["name"], costmod.fmt_money(gain), " at the low end" if low_end else ""))
    return notes
