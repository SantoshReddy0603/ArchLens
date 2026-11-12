"""Input validation and normalisation (spec Section 3).

validate(scenario) -> (normalized, validation_errors, assumptions)

Rules:
  * never raises, whatever JSON it is given (including non-objects);
  * every problem is reported with its field path; nothing is silently coerced
    except unambiguous cases (case/whitespace of enums, whole-number floats,
    numeric availability), and each coercion is logged in assumptions;
  * numbers given as strings and booleans given as numbers are rejected;
  * optional fields get the documented default and an assumption line;
  * soft inconsistencies (peak > users, users shrinking, api_only mixed with
    client platforms, unknown keys) become warnings in normalized["_warnings"].
"""
import math

from .derive import canonical_language, IGNORED_LANGUAGES

ENUMS = {
    "project.type": ["content_site", "web_app", "saas_b2b", "ecommerce", "marketplace", "social",
                     "realtime_collab", "fintech", "healthtech", "data_analytics", "iot",
                     "ai_ml_product", "internal_tool", "gaming", "api_platform", "other"],
    "project.platforms": ["web", "android", "ios", "desktop", "api_only"],
    "scale.traffic_pattern": ["steady", "spiky", "seasonal"],
    "scale.workload": ["read_heavy", "balanced", "write_heavy"],
    "team.experience": ["beginner", "intermediate", "senior", "mixed"],
    "infrastructure.cloud_preference": ["any", "aws", "gcp", "azure", "none"],
    "infrastructure.deployment_model": ["managed_cloud", "self_hosted_cloud_vm", "on_premise", "air_gapped"],
    "infrastructure.data_residency": ["none", "india", "eu", "us", "other"],
    "requirements.auth": ["none", "email_password", "social", "phone_otp", "enterprise_sso"],
    "requirements.ml_features": ["none", "inference_api", "llm_rag", "training"],
    "requirements.availability": ["99", "99.9", "99.95", "99.99"],
    "compliance": ["gdpr", "dpdp", "hipaa", "pci_dss", "rbi_localisation", "soc2", "iso27001"],
}
PRIORITY_KEYS = ["cost", "time_to_market", "scalability", "performance", "maintainability", "security"]
BOOL_REQUIREMENTS = ["realtime", "offline_support", "full_text_search", "file_media_storage",
                     "payments", "multi_tenant", "seo_critical"]
EXTRA_ENUMS = {
    "learning_appetite": (["low", "medium", "high"], "medium"),
    "lock_in_tolerance": (["low", "medium", "high"], "medium"),
    "user_geography": (["single_city", "country", "multi_region", "global"], None),
    "data_sensitivity": (["public", "internal", "personal", "financial", "health"], None),
    "budget_strictness": (["flexible", "firm", "hard"], "firm"),
    "hiring_plan": (["none", "slow", "aggressive"], "none"),
    "mobile_preference": (["no_preference", "native", "cross_platform", "pwa"], "no_preference"),
}
KNOWN_SECTIONS = ["scenario_id", "project", "scale", "team", "budget", "timeline", "infrastructure",
                  "requirements", "compliance", "priorities", "extra"]
KNOWN_KEYS = {
    "project": ["name", "type", "description", "platforms"],
    "scale": ["users_at_launch", "users_12_months", "peak_concurrent_users", "traffic_pattern",
              "workload", "data_volume_gb_12_months"],
    "team": ["size", "experience", "languages", "has_devops"],
    "budget": ["monthly_infra_usd"],
    "timeline": ["mvp_weeks"],
    "infrastructure": ["cloud_preference", "deployment_model", "data_residency", "existing_systems"],
    "requirements": ["realtime", "offline_support", "full_text_search", "file_media_storage", "payments",
                     "auth", "multi_tenant", "ml_features", "seo_critical", "availability", "p95_latency_ms"],
    "priorities": PRIORITY_KEYS,
    "extra": list(EXTRA_ENUMS) + ["existing_licenses", "expected_rps", "cloud_credits_usd"],
}
MAX_COUNT = 10 ** 10


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _type_name(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "a boolean"
    if isinstance(v, (int, float)):
        return "a number"
    if isinstance(v, str):
        return "a string"
    if isinstance(v, list):
        return "a list"
    return "an object"


class _Ctx:
    def __init__(self):
        self.errors = []
        self.assumptions = []
        self.warnings = []

    def err(self, field, msg):
        self.errors.append({"field": field, "message": msg})

    def assume(self, msg):
        self.assumptions.append(msg)

    def warn(self, code, severity, msg, fields):
        self.warnings.append({"code": code, "severity": severity, "message": msg, "fields": fields})


def _section(raw, name, ctx, required):
    sec = raw.get(name)
    if sec is None:
        if required:
            ctx.err(name, "section '%s' is missing; its required fields cannot be read" % name)
        return {}
    if not isinstance(sec, dict):
        ctx.err(name, "must be an object, got %s" % _type_name(sec))
        return {}
    for k in sorted(sec):
        if k not in KNOWN_KEYS[name]:
            ctx.warn("UNKNOWN_FIELD", "info", "Field '%s.%s' is not part of the schema and is ignored." % (name, k),
                     ["%s.%s" % (name, k)])
    return sec


def _int(sec, key, path, ctx, required, default=None, minimum=0, allow_null=False):
    v = sec.get(key)
    if key not in sec or v is None:
        if v is None and key in sec and allow_null:
            return None
        if required:
            ctx.err(path, "required field is missing" if key not in sec else "must not be null")
        elif default is not None:
            ctx.assume("%s not provided; defaulted to %s." % (path, default))
        return default
    if isinstance(v, bool) or not _is_num(v):
        got = "a numeric string (quote marks not allowed)" if isinstance(v, str) and _looks_numeric(v) else _type_name(v)
        ctx.err(path, "must be an integer >= %s, got %s" % (minimum, got))
        return default
    if v != int(v):
        ctx.err(path, "must be a whole number, got %s" % v)
        return default
    v = int(v)
    if v < minimum:
        ctx.err(path, "must be >= %s, got %s" % (minimum, v))
        return default
    if v > MAX_COUNT:
        ctx.err(path, "value %s is implausibly large" % v)
        return default
    return v


def _looks_numeric(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def _number(sec, key, path, ctx, required, default=None, minimum=0.0):
    if key not in sec:
        if required:
            ctx.err(path, "required field is missing")
        elif default is not None:
            ctx.assume("%s not provided; defaulted to %s." % (path, default))
        return default
    v = sec[key]
    if not _is_num(v):
        got = "a numeric string (quote marks not allowed)" if isinstance(v, str) and _looks_numeric(v) else _type_name(v)
        ctx.err(path, "must be a number >= %s, got %s" % (minimum, got))
        return default
    if v < minimum:
        ctx.err(path, "must be >= %s, got %s" % (minimum, v))
        return default
    return float(v) if v != int(v) else int(v)


def _bool(sec, key, path, ctx, default=False):
    if key not in sec:
        ctx.assume("%s not provided; defaulted to %s." % (path, str(default).lower()))
        return default
    v = sec[key]
    if not isinstance(v, bool):
        ctx.err(path, "must be true or false, got %s" % _type_name(v))
        return default
    return v


def _enum(sec, key, path, ctx, allowed, required=False, default=None, enum_path=None):
    if key not in sec:
        if required:
            ctx.err(path, "required field is missing; allowed values: %s" % ", ".join(allowed))
        elif default is not None:
            ctx.assume("%s not provided; defaulted to '%s'." % (path, default))
        return default
    v = sec[key]
    if not isinstance(v, str):
        ctx.err(path, "must be a string, one of: %s (got %s)" % (", ".join(allowed), _type_name(v)))
        return default
    s = v.strip().lower()
    if s not in allowed:
        ctx.err(path, "unknown value '%s'; allowed values: %s" % (v, ", ".join(allowed)))
        return default
    if s != v:
        ctx.assume("%s value '%s' normalised to '%s'." % (path, v, s))
    return s


def _enum_list(sec, key, path, ctx, allowed, required, non_empty):
    if key not in sec:
        if required:
            ctx.err(path, "required field is missing; allowed values: %s" % ", ".join(allowed))
        else:
            ctx.assume("%s not provided; defaulted to an empty list." % path)
        return []
    v = sec[key]
    if not isinstance(v, list):
        ctx.err(path, "must be a list of strings, got %s" % _type_name(v))
        return []
    out = []
    for i, item in enumerate(v):
        if not isinstance(item, str):
            ctx.err("%s[%d]" % (path, i), "must be a string, got %s" % _type_name(item))
            continue
        s = item.strip().lower()
        if s not in allowed:
            ctx.err("%s[%d]" % (path, i), "unknown value '%s'; allowed values: %s" % (item, ", ".join(allowed)))
            continue
        if s in out:
            ctx.assume("%s lists '%s' more than once; duplicate removed." % (path, s))
            continue
        out.append(s)
    if non_empty and not v:
        ctx.err(path, "must contain at least one value; allowed values: %s" % ", ".join(allowed))
    return out


def _string_list(sec, key, path, ctx, required):
    if key not in sec:
        if required:
            ctx.err(path, "required field is missing (use an empty list if none)")
        else:
            ctx.assume("%s not provided; defaulted to an empty list." % path)
        return []
    v = sec[key]
    if not isinstance(v, list):
        ctx.err(path, "must be a list of strings, got %s" % _type_name(v))
        return []
    out = []
    for i, item in enumerate(v):
        if not isinstance(item, str):
            ctx.err("%s[%d]" % (path, i), "must be a string, got %s" % _type_name(item))
        elif item.strip():
            out.append(item.strip())
    return out


def _languages(sec, ctx):
    raw = _string_list(sec, "languages", "team.languages", ctx, True)
    canon, ignored, unknown = [], [], []
    for lang in raw:
        c = canonical_language(lang)
        if c in IGNORED_LANGUAGES:
            ignored.append(lang)
        elif c not in canon:
            canon.append(c)
    if ignored:
        ctx.assume("team.languages entries %s describe markup or data formats, not programming skills, and are not counted."
                   % ", ".join("'%s'" % x for x in ignored))
    if canon != [x.strip().lower() for x in raw if canonical_language(x) not in IGNORED_LANGUAGES]:
        ctx.assume("team.languages aliases were normalised to canonical names: %s." % ", ".join(canon))
    return canon


def _availability(sec, ctx):
    allowed = ENUMS["requirements.availability"]
    if "availability" not in sec:
        ctx.assume("requirements.availability not provided; defaulted to '99.9'.")
        return "99.9"
    v = sec["availability"]
    if _is_num(v):
        s = ("%g" % v)
        if s in allowed:
            ctx.assume("requirements.availability given as the number %s; read as the string '%s'." % (v, s))
            return s
    if isinstance(v, str) and v.strip().rstrip("%") in allowed:
        s = v.strip().rstrip("%")
        if s != v:
            ctx.assume("requirements.availability '%s' normalised to '%s'." % (v, s))
        return s
    ctx.err("requirements.availability",
            "must be one of the strings %s, got %s" % (", ".join('"%s"' % a for a in allowed),
                                                       repr(v) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else _type_name(v)))
    return "99.9"


def _latency(sec, ctx):
    if "p95_latency_ms" not in sec or sec["p95_latency_ms"] is None:
        if "p95_latency_ms" not in sec:
            ctx.assume("requirements.p95_latency_ms not provided; no latency target is assumed.")
        return None
    v = sec["p95_latency_ms"]
    if not _is_num(v) or v != int(v) or v <= 0:
        ctx.err("requirements.p95_latency_ms", "must be a positive integer or null, got %s" % (repr(v) if _is_num(v) or isinstance(v, str) else _type_name(v)))
        return None
    return int(v)


def _priorities(raw, ctx):
    sec = raw.get("priorities")
    out = {}
    if sec is None:
        ctx.assume("priorities not provided; every priority defaulted to 3.")
        sec = {}
    elif not isinstance(sec, dict):
        ctx.err("priorities", "must be an object with keys %s, got %s" % (", ".join(PRIORITY_KEYS), _type_name(sec)))
        sec = {}
    else:
        for k in sorted(sec):
            if k not in PRIORITY_KEYS:
                ctx.warn("UNKNOWN_FIELD", "info", "Priority '%s' is not recognised and is ignored." % k, ["priorities.%s" % k])
    for k in PRIORITY_KEYS:
        path = "priorities.%s" % k
        if k not in sec:
            if raw.get("priorities") is not None:
                ctx.assume("%s not provided; defaulted to 3." % path)
            out[k] = 3
            continue
        v = sec[k]
        if isinstance(v, bool) or not _is_num(v) or v != int(v):
            ctx.err(path, "must be an integer from 1 to 5, got %s" % (repr(v) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else _type_name(v)))
            out[k] = 3
        elif not 1 <= int(v) <= 5:
            ctx.err(path, "must be between 1 and 5, got %s" % int(v))
            out[k] = 3
        else:
            out[k] = int(v)
    return out


def _extra(raw, ctx):
    sec = raw.get("extra")
    out = {}
    if sec is not None and not isinstance(sec, dict):
        ctx.err("extra", "must be an object, got %s" % _type_name(sec))
        sec = None
    if sec is None:
        ctx.assume("No extra inputs supplied; neutral defaults are used (learning appetite medium, lock-in tolerance "
                   "medium, budget strictness firm, no hiring plan, no mobile preference).")
        sec = {}
    else:
        _section(raw, "extra", ctx, False)
    for key, (allowed, default) in EXTRA_ENUMS.items():
        path = "extra.%s" % key
        if key in sec and sec[key] is not None:
            out[key] = _enum(sec, key, path, ctx, allowed, default=default)
        else:
            out[key] = default
    if "existing_licenses" in sec:
        out["existing_licenses"] = [s.lower() for s in _string_list(sec, "existing_licenses", "extra.existing_licenses", ctx, False)]
    else:
        out["existing_licenses"] = []
    if sec.get("expected_rps") is not None:
        out["expected_rps"] = _number(sec, "expected_rps", "extra.expected_rps", ctx, False, minimum=0)
    else:
        out["expected_rps"] = None
    if sec.get("cloud_credits_usd") is not None:
        out["cloud_credits_usd"] = _number(sec, "cloud_credits_usd", "extra.cloud_credits_usd", ctx, False, minimum=0)
    else:
        out["cloud_credits_usd"] = None
    return out


def validate(scenario):
    """Validate and normalise a scenario. Returns (normalized, validation_errors, assumptions)."""
    ctx = _Ctx()
    if not isinstance(scenario, dict):
        ctx.err("$", "the scenario must be a JSON object, got %s" % _type_name(scenario))
        return {"_warnings": []}, ctx.errors, ctx.assumptions
    raw = scenario
    for k in sorted(raw, key=str):
        if k not in KNOWN_SECTIONS:
            ctx.warn("UNKNOWN_FIELD", "info", "Top-level field '%s' is not part of the schema and is ignored." % k, [str(k)])

    n = {}
    sid = raw.get("scenario_id")
    if sid is None:
        ctx.assume("scenario_id not provided; reported as 'unnamed'.")
        n["scenario_id"] = "unnamed"
    elif isinstance(sid, (str, int)) and not isinstance(sid, bool):
        n["scenario_id"] = str(sid)
    else:
        ctx.err("scenario_id", "must be a string, got %s" % _type_name(sid))
        n["scenario_id"] = "unnamed"

    p = _section(raw, "project", ctx, True)
    name = p.get("name")
    if name is not None and not isinstance(name, str):
        ctx.err("project.name", "must be a string, got %s" % _type_name(name))
        name = None
    desc = p.get("description")
    if desc is not None and not isinstance(desc, str):
        ctx.err("project.description", "must be a string, got %s" % _type_name(desc))
        desc = None
    n["project"] = {
        "name": name or "Untitled project",
        "type": _enum(p, "type", "project.type", ctx, ENUMS["project.type"], required=True),
        "description": desc or "",
        "platforms": _enum_list(p, "platforms", "project.platforms", ctx, ENUMS["project.platforms"], True, True),
    }
    if name is None:
        ctx.assume("project.name not provided; shown as 'Untitled project'.")

    s = _section(raw, "scale", ctx, True)
    scale = {
        "users_at_launch": _int(s, "users_at_launch", "scale.users_at_launch", ctx, True),
        "users_12_months": _int(s, "users_12_months", "scale.users_12_months", ctx, True),
        "peak_concurrent_users": _int(s, "peak_concurrent_users", "scale.peak_concurrent_users", ctx, False, allow_null=True),
        "traffic_pattern": _enum(s, "traffic_pattern", "scale.traffic_pattern", ctx, ENUMS["scale.traffic_pattern"], default="steady"),
        "workload": _enum(s, "workload", "scale.workload", ctx, ENUMS["scale.workload"], default="balanced"),
        "data_volume_gb_12_months": _number(s, "data_volume_gb_12_months", "scale.data_volume_gb_12_months", ctx, False, minimum=0),
    }
    if "peak_concurrent_users" not in s or s.get("peak_concurrent_users") is None:
        if scale["users_12_months"] is not None:
            ctx.assume("scale.peak_concurrent_users not provided; it is estimated later from users_12_months and traffic_pattern.")
    if scale["data_volume_gb_12_months"] is None and "data_volume_gb_12_months" not in s:
        ctx.assume("scale.data_volume_gb_12_months not provided; estimated later from users and project type.")
    n["scale"] = scale

    t = _section(raw, "team", ctx, True)
    n["team"] = {
        "size": _int(t, "size", "team.size", ctx, True, minimum=1),
        "experience": _enum(t, "experience", "team.experience", ctx, ENUMS["team.experience"], required=True),
        "languages": _languages(t, ctx),
        "has_devops": _bool(t, "has_devops", "team.has_devops", ctx, False),
    }

    b = _section(raw, "budget", ctx, True)
    n["budget"] = {"monthly_infra_usd": _number(b, "monthly_infra_usd", "budget.monthly_infra_usd", ctx, True, minimum=0)}
    tl = _section(raw, "timeline", ctx, True)
    n["timeline"] = {"mvp_weeks": _int(tl, "mvp_weeks", "timeline.mvp_weeks", ctx, True, minimum=1)}

    i = _section(raw, "infrastructure", ctx, False)
    es = _string_list(i, "existing_systems", "infrastructure.existing_systems", ctx, False)
    n["infrastructure"] = {
        "cloud_preference": _enum(i, "cloud_preference", "infrastructure.cloud_preference", ctx, ENUMS["infrastructure.cloud_preference"], default="any"),
        "deployment_model": _enum(i, "deployment_model", "infrastructure.deployment_model", ctx, ENUMS["infrastructure.deployment_model"], default="managed_cloud"),
        "data_residency": _enum(i, "data_residency", "infrastructure.data_residency", ctx, ENUMS["infrastructure.data_residency"], default="none"),
        "existing_systems": es,
    }

    r = _section(raw, "requirements", ctx, False)
    req = {k: _bool(r, k, "requirements.%s" % k, ctx, False) for k in BOOL_REQUIREMENTS}
    req["auth"] = _enum(r, "auth", "requirements.auth", ctx, ENUMS["requirements.auth"], default="none")
    req["ml_features"] = _enum(r, "ml_features", "requirements.ml_features", ctx, ENUMS["requirements.ml_features"], default="none")
    req["availability"] = _availability(r, ctx)
    req["p95_latency_ms"] = _latency(r, ctx)
    n["requirements"] = req

    n["compliance"] = _enum_list(raw, "compliance", "compliance", ctx, ENUMS["compliance"], False, False)
    n["priorities"] = _priorities(raw, ctx)
    n["extra"] = _extra(raw, ctx)

    _soft_checks(n, ctx)
    n["_warnings"] = ctx.warnings
    return n, ctx.errors, ctx.assumptions


def _soft_checks(n, ctx):
    sc = n["scale"]
    launch, y1, peak = sc["users_at_launch"], sc["users_12_months"], sc["peak_concurrent_users"]
    if launch is not None and y1 is not None and y1 < launch:
        ctx.warn("USERS_SHRINKING", "warning",
                 "users_12_months (%s) is lower than users_at_launch (%s); the larger number is used for sizing." % (y1, launch),
                 ["scale.users_12_months", "scale.users_at_launch"])
    top = max(v for v in (launch, y1) if v is not None) if (launch is not None or y1 is not None) else None
    if peak is not None and top is not None and peak > top:
        ctx.warn("PEAK_EXCEEDS_USERS", "warning",
                 "peak_concurrent_users (%s) is higher than the total number of users (%s); check the numbers." % (peak, top),
                 ["scale.peak_concurrent_users", "scale.users_12_months"])
    plats = n["project"]["platforms"]
    if "api_only" in plats and len(plats) > 1:
        ctx.warn("PLATFORM_API_ONLY_MIX", "warning",
                 "api_only is combined with client platforms (%s); a client frontend is still planned for those platforms."
                 % ", ".join(p for p in plats if p != "api_only"), ["project.platforms"])
