"""Derived project profile: every number the scoring model uses, computed deterministically
from the normalised input. No randomness, no clock, no environment access.

Main outputs
  scale_need      0-10 continuous demand level (log scale of peak load and user base)
  ops_capacity    0-1 how much infrastructure work the team can absorb
  time_pressure   0-1 from the ratio of estimated effort to available person-weeks
  weights         criterion weights = priority x context multiplier, renormalised
"""
import math
import re

LANG_ALIASES = {
    "js": "javascript", "javascript": "javascript", "node": "javascript", "nodejs": "javascript",
    "node.js": "javascript", "ecmascript": "javascript",
    "ts": "typescript", "typescript": "typescript",
    "c#": "csharp", "csharp": "csharp", "c-sharp": "csharp", "dotnet": "csharp", ".net": "csharp",
    "golang": "go", "go": "go", "py": "python", "python3": "python", "python": "python",
    "kt": "kotlin", "kotlin": "kotlin", "java": "java", "php": "php", "rb": "ruby", "ruby": "ruby",
    "rust": "rust", "swift": "swift", "dart": "dart", "flutter": "dart", "elixir": "elixir",
    "scala": "java", "c++": "cpp", "cpp": "cpp", "sql": "sql",
}
IGNORED_LANGUAGES = {"html", "css", "markdown", "md", "json", "yaml", "yml", "xml", "shell", "bash", "sh", "scss", "sass"}

# Directed transfer of skills: (known, needed) -> fit in [0, 1]
SKILL_AFFINITY = {
    ("typescript", "javascript"): 1.0, ("javascript", "typescript"): 0.85,
    ("java", "kotlin"): 0.8, ("kotlin", "java"): 0.9,
    ("csharp", "java"): 0.45, ("java", "csharp"): 0.45, ("csharp", "kotlin"): 0.35, ("kotlin", "csharp"): 0.35,
    ("python", "ruby"): 0.3, ("ruby", "python"): 0.3, ("javascript", "dart"): 0.3, ("typescript", "dart"): 0.35,
    ("go", "rust"): 0.3, ("rust", "go"): 0.4,
}
EXP_FACTOR = {"beginner": 0.0, "intermediate": 0.5, "mixed": 0.4, "senior": 1.0}
EXP_PRODUCTIVITY = {"beginner": 0.7, "intermediate": 1.0, "mixed": 0.9, "senior": 1.25}
APPETITE = {"low": 0.0, "medium": 0.5, "high": 1.0}
BEGINNER_SENSITIVITY = {"beginner": 1.0, "mixed": 0.4, "intermediate": 0.15, "senior": 0.0}
AVAIL_LEVEL = {"99": 2.0, "99.9": 3.0, "99.95": 3.301, "99.99": 4.0}
TRAFFIC_AVG_RATIO = {"steady": 0.25, "seasonal": 0.15, "spiky": 0.10}
PEAK_SHARE = {"steady": 0.03, "seasonal": 0.05, "spiky": 0.08}
REQ_PER_USER_MONTH = {
    "content_site": 30, "internal_tool": 400, "web_app": 300, "saas_b2b": 600, "ecommerce": 200,
    "marketplace": 250, "social": 1500, "realtime_collab": 3000, "fintech": 150, "healthtech": 150,
    "data_analytics": 800, "iot": 5000, "ai_ml_product": 300, "gaming": 3000, "api_platform": 2000, "other": 200,
}
PAYLOAD_KB = {"content_site": 500, "ecommerce": 150, "marketplace": 40, "social": 100}
GB_PER_USER = {"content_site": 0.0003, "social": 0.02, "marketplace": 0.002, "ecommerce": 0.001}
TYPE_EFFORT_PW = {  # person-weeks of build effort for one client platform
    "content_site": 0.8, "internal_tool": 3, "web_app": 6, "saas_b2b": 8, "ecommerce": 8, "marketplace": 6,
    "social": 10, "realtime_collab": 8, "fintech": 10, "healthtech": 10, "data_analytics": 8, "iot": 8,
    "ai_ml_product": 8, "gaming": 12, "api_platform": 6, "other": 4,
}
FEATURE_EFFORT_PW = {"payments": 2.5, "realtime": 2.5, "offline_support": 3, "full_text_search": 1,
                     "file_media_storage": 1, "multi_tenant": 2, "seo_critical": 0.3}
AUTH_EFFORT = {"none": 0, "email_password": 1, "social": 1, "phone_otp": 1, "enterprise_sso": 2}
ML_EFFORT = {"none": 0, "inference_api": 2, "llm_rag": 3, "training": 6}

LAYER_WEIGHTS = {"backend": 0.30, "hosting": 0.30, "database": 0.25, "frontend": 0.15}
STATIC_LAYER_WEIGHTS = {"frontend": 0.45, "hosting": 0.30, "database": 0.10, "backend": 0.15}

HINT_PATTERNS = {
    "low_end_devices": r"low[- ]end|entry[- ]level phone|\b[12] ?gb ram|budget phones?",
    "poor_connectivity": r"\b2g\b|\b3g\b|intermittent|patchy|villages?|rural|poor (network|connectivity)|no signal",
    "upi": r"\bupi\b",
    "location": r"\bgps\b|geo[- ]?tag|geolocat|live location|location (tracking|history)|track(s|ing)? (the )?(rider|driver|vehicle|agent)s?",
    "tabular_ml": r"credit[- ]scor|scoring model|risk model|fraud|churn|underwrit|forecast|propensity",
    "approvals": r"\bapprov|leave requests?|sign[- ]?offs?|escalat|reimburse|expense claims?",
    "ledger": r"double[- ]entry|\b(?:general|financial|accounting|transaction|payments?|wallet|loan)s? ledgers?\b",
}


def canonical_language(s):
    k = s.strip().lower()
    return LANG_ALIASES.get(k, k)


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def skill_fit_lang(profile, lang):
    """Fit in [0,1] of one language for this team: known = 1, adjacent skills partial, else a floor
    raised by learning appetite and seniority."""
    known = profile["skills"]
    best = 0.0
    for k in known:
        best = max(best, 1.0 if k == lang else SKILL_AFFINITY.get((k, lang), 0.0))
    floor = 0.10 + 0.25 * profile["appetite"] + 0.15 * profile["exp_factor"]
    return max(best, floor)


def skill_fit_component(profile, comp):
    """Language fit of a component (best of its languages) reduced by a learning-curve cost that bites
    beginners and short timelines. Components without a language (static generators, None) fit at 1."""
    langs = comp.get("languages") or []
    fit = 1.0 if not langs else max(skill_fit_lang(profile, l) for l in langs)
    learn = comp.get("learning_curve", 3) / 10.0
    pen = 0.35 * profile["beginner_sens"] * learn + 0.15 * profile["time_pressure"] * learn
    return _clamp(fit * (1.0 - pen))


def _loglerp_need(x):
    return math.log10(x + 1.0)


def build_profile(n):
    """Build the derived profile from a normalised scenario (output of validate)."""
    proj, sc, team, bud, tl = n["project"], n["scale"], n["team"], n["budget"], n["timeline"]
    infra, req, extra = n["infrastructure"], n["requirements"], n["extra"]
    comp = set(n["compliance"])
    ptype = proj["type"]
    notes = []

    users12 = max(sc["users_12_months"], sc["users_at_launch"])
    pattern, workload = sc["traffic_pattern"], sc["workload"]
    peak_given = sc["peak_concurrent_users"]
    peak_est = max(1, int(math.ceil(users12 * PEAK_SHARE[pattern])))
    peak = peak_given if peak_given is not None else peak_est
    peak_floor = 0.02 * users12
    peak_eff = max(peak, peak_floor)
    rps_peak = extra["expected_rps"] if extra.get("expected_rps") else peak_eff * 0.1
    rps_peak = max(rps_peak, 0.05)
    avg_rps = rps_peak * TRAFFIC_AVG_RATIO[pattern]
    req_a = avg_rps * 2.6e6
    req_b = users12 * REQ_PER_USER_MONTH[ptype]
    monthly_requests = math.sqrt(max(req_a, 1.0) * max(req_b, 1.0))
    egress_gb = monthly_requests * PAYLOAD_KB.get(ptype, 30) / 1e6
    data_gb = sc["data_volume_gb_12_months"]
    if data_gb is None:
        data_gb = users12 * GB_PER_USER.get(ptype, 0.001)

    need = 1.6 * math.log10(max(peak_eff, rps_peak * 3, 1.0) + 1.0)
    need *= {"read_heavy": 0.95, "balanced": 1.0, "write_heavy": 1.1}[workload]
    need *= {"steady": 1.0, "seasonal": 1.05, "spiky": 1.1}[pattern]
    if req["realtime"]:
        need *= 1.1
    scale_need = _clamp(need, 0.0, 10.0)

    exp = team["experience"]
    ops = 0.10 + 0.50 * team["has_devops"] + 0.20 * EXP_FACTOR[exp] + 0.15 * min(team["size"], 8) / 8.0
    ops_capacity = _clamp(ops)

    client_platforms = [p for p in proj["platforms"] if p != "api_only"]
    mobile = [p for p in client_platforms if p in ("android", "ios")]
    if extra.get("mobile_preference") == "pwa" and mobile and ptype != "gaming":
        client_platforms = sorted(set(client_platforms) - set(mobile) | {"web"})
        notes.append("mobile_preference is pwa, so %s %s served by one installable web app instead of store apps%s."
                     % (" and ".join(m.replace("ios", "iOS").replace("android", "Android") for m in mobile), "is" if len(mobile) == 1 else "are",
                        "; push and background sync on iOS need iOS 16.4+ and a home-screen install" if "ios" in mobile else ""))
    api_only = not client_platforms

    needs_server = bool(req["auth"] != "none" or req["payments"] or req["multi_tenant"] or req["ml_features"] != "none"
                        or req["file_media_storage"] or req["realtime"] or ptype != "content_site" or api_only)
    needs_persist = needs_server
    static_candidate = (ptype == "content_site" and not needs_server and not api_only)

    effort = TYPE_EFFORT_PW[ptype] + AUTH_EFFORT[req["auth"]] + ML_EFFORT[req["ml_features"]] + 0.5 * len(comp)
    for key, w in FEATURE_EFFORT_PW.items():
        if req.get(key):
            effort += w
    platform_mult = 1.0 + 0.6 * max(0, len(client_platforms) - 1) if client_platforms else 0.7
    effort_pw = effort * platform_mult
    capacity_pw = team["size"] * EXP_PRODUCTIVITY[exp] * 0.65 * tl["mvp_weeks"]
    effort_ratio = effort_pw / max(capacity_pw, 0.1)
    time_pressure = _clamp((effort_ratio - 0.4) / 1.6)

    budget = float(bud["monthly_infra_usd"])
    budget_tier = 0 if budget <= 0 else 1 if budget < 50 else 2 if budget < 200 else 3 if budget < 1000 else 4 if budget < 5000 else 5
    budget_tightness = _clamp(1.0 - math.log10(budget + 1.0) / 4.0)

    avail_level = AVAIL_LEVEL[req["availability"]]
    p95 = req["p95_latency_ms"]
    latency_need = 0.25 if p95 is None else _clamp(1.0 - (math.log10(p95) - 1.7) / 1.3)

    residency = infra["data_residency"]
    if "rbi_localisation" in comp and residency in ("none", "other"):
        notes.append("rbi_localisation requires payment data to stay in India, so the effective data residency is india.")
        residency = "india"
    regulated = len(comp)

    desc = proj["description"].lower()
    hints = {k: bool(re.search(p, desc)) for k, p in HINT_PATTERNS.items()}
    if hints["low_end_devices"]:
        notes.append("Description mentions low-end devices; client footprint is weighted higher.")
    if hints["poor_connectivity"]:
        notes.append("Description mentions poor connectivity; sync and payload size are weighted higher.")

    geography = extra.get("user_geography")
    if not geography:
        geography = "country" if residency in ("india", "eu", "us") else "global"
    sensitivity = extra.get("data_sensitivity")
    if not sensitivity:
        sensitivity = ("health" if "hipaa" in comp or ptype == "healthtech" else
                       "financial" if (req["payments"] or "pci_dss" in comp or ptype == "fintech") else
                       "personal" if (req["auth"] != "none" or comp) else
                       "internal" if ptype == "internal_tool" else "public")

    priorities = n["priorities"]
    mult = {
        "cost": 1.0 + budget_tightness,
        "time_to_market": 1.0 + time_pressure,
        "scalability": 0.75 + 0.75 * min(1.0, scale_need / 6.0),
        "performance": 1.0 + latency_need + (0.3 if hints["low_end_devices"] else 0.0)
        + (0.15 if extra.get("user_geography") in ("multi_region", "global") else 0.0),
        "maintainability": 1.0 + (0.25 if extra.get("hiring_plan") == "aggressive" else 0.0),
        "security": 1.0 + 0.15 * regulated + {"public": -0.1, "personal": 0.05, "financial": 0.2, "health": 0.2}.get(sensitivity, 0.0)
        if extra.get("data_sensitivity") else 1.0 + 0.15 * regulated + (0.2 if sensitivity in ("financial", "health") else 0.0),
    }
    raw_w = {k: priorities[k] * mult[k] for k in priorities}
    total = sum(raw_w.values())
    weights = {k: raw_w[k] / total for k in sorted(raw_w)}

    allowed_complexity = min(10.0, 3.5 + 0.45 * scale_need + 0.12 * (min(team["size"], 10) - 1) + 1.5 * ops_capacity)
    if static_candidate:
        allowed_complexity = min(allowed_complexity, 3.0 + 0.3 * scale_need)

    return {
        "project_type": ptype, "client_platforms": client_platforms, "api_only": api_only,
        "users_launch": sc["users_at_launch"], "users_12m": users12, "peak_concurrent": peak,
        "peak_estimated": peak_given is None, "peak_eff": peak_eff, "peak_rps": rps_peak, "avg_rps": avg_rps,
        "monthly_requests": monthly_requests, "egress_gb": egress_gb, "data_gb": data_gb,
        "traffic_pattern": pattern, "workload": workload,
        "scale_need": scale_need, "scale_tier": min(5, int(scale_need / 1.8)),
        "team_size": team["size"], "experience": exp, "exp_factor": EXP_FACTOR[exp], "has_devops": team["has_devops"],
        "ops_capacity": ops_capacity, "skills": list(team["languages"]),
        "appetite": APPETITE[extra["learning_appetite"]], "beginner_sens": BEGINNER_SENSITIVITY[exp],
        "lock_in_tolerance": extra["lock_in_tolerance"], "hiring_plan": extra["hiring_plan"],
        "mobile_preference": extra["mobile_preference"], "licenses": list(extra["existing_licenses"]),
        "effort_pw": effort_pw, "capacity_pw": capacity_pw, "effort_ratio": effort_ratio, "time_pressure": time_pressure,
        "mvp_weeks": tl["mvp_weeks"],
        "budget": budget, "budget_tier": budget_tier, "budget_tightness": budget_tightness,
        "budget_strictness": extra["budget_strictness"],
        "availability": req["availability"], "avail_level": avail_level,
        "avail_required": 4.0 + 1.8 * (avail_level - 2.0), "p95": p95, "latency_need": latency_need,
        "residency": residency, "compliance": sorted(comp), "regulated_count": regulated,
        "deployment": infra["deployment_model"], "cloud_pref": infra["cloud_preference"],
        "existing_systems": list(infra["existing_systems"]),
        "license_texts": [x.replace("_", " ") for x in extra["existing_licenses"]],
        "cloud_credits_usd": extra.get("cloud_credits_usd"),
        "realtime": req["realtime"], "offline": req["offline_support"], "search": req["full_text_search"],
        "files": req["file_media_storage"], "payments": req["payments"], "auth": req["auth"],
        "multi_tenant": req["multi_tenant"], "ml": req["ml_features"], "seo": req["seo_critical"],
        "needs_server_logic": needs_server, "needs_persistence": needs_persist, "static_candidate": static_candidate,
        "geography": geography, "data_sensitivity": sensitivity, "hints": hints,
        "india_ctx": residency == "india" or bool(comp & {"dpdp", "rbi_localisation"}) or hints["upi"],
        "weights": weights, "weight_multipliers": mult,
        "layer_weights": dict(STATIC_LAYER_WEIGHTS if static_candidate else LAYER_WEIGHTS),
        "allowed_complexity": allowed_complexity, "notes": notes,
    }


def reuse_source(integration, p):
    """'system' when an existing system matches the integration, 'licence' when only an owned licence or
    cloud credit does, else None. Both earn the reuse bonus; only systems need a connector or migration."""
    if any(integration["_re"].search(s) for s in p["existing_systems"]):
        return "system"
    if any(integration["_re"].search(s) for s in p.get("license_texts", [])):
        return "licence"
    return None
