"""Hard constraints and component compatibility.

Layer filters remove single components that can never satisfy a hard constraint; stack filters check
pairings (runtime on host, database access path, residency of the chosen path). Every elimination
returns a plain-language reason, so nothing is dropped silently."""

HARD_CERTS = ("hipaa", "pci_dss")
CLOUDS = ("aws", "gcp", "azure")
NONE_IDS = ("none_fe", "none_be", "none_db", "none_host")


def is_none(comp):
    return comp["id"] in NONE_IDS


def region_ok(regions, residency):
    if residency in ("none", "other"):
        return True
    return "*" in regions or residency in regions


def _self_contained(comp):
    return comp.get("self_hostable", False) and not comp.get("requires_saas", False)


def _certs_missing(certs, comp_certs):
    return [c for c in certs if c not in comp_certs]


def component_reasons(layer, comp, p):
    """Reasons (possibly empty) why one component cannot be used for this profile, independent of the rest."""
    r = []
    dep, pref, res = p["deployment"], p["cloud_pref"], p["residency"]
    attrs = comp.get("attrs", {})
    restricted = dep in ("on_premise", "air_gapped")
    if layer == "backend" and p["needs_server_logic"] and comp["id"] == "none_be":
        r.append("the project needs server-side logic (accounts, payments, uploads, realtime or non-content features)")
    if layer == "database" and p["needs_persistence"] and comp["id"] == "none_db":
        r.append("the project needs persistent data")
    if layer == "frontend":
        if p["api_only"] and comp["id"] != "none_fe":
            r.append("the project is API-only, so no client application is built")
        elif not p["api_only"] and comp["id"] == "none_fe":
            r.append("the project has client platforms to build")
        if p["offline"] and not p["api_only"] and attrs.get("offline", 0) < 0.35 and not is_none(comp):
            r.append("offline support is required but this client has too little offline capability")
    if layer == "frontend" and attrs.get("game_client") and p["project_type"] != "gaming":
        r.append("a game engine client is only for game projects")
    if layer == "backend" and attrs.get("game_server") and p["project_type"] != "gaming":
        r.append("a dedicated game server is only for game projects")
    if layer == "database" and attrs.get("kind") in ("analytics", "timeseries") and p["project_type"] not in ("data_analytics", "iot"):
        r.append("an analytics or time-series store does not serve as the main transactional database")
    if layer in ("backend", "database") and not is_none(comp):
        if restricted and not _self_contained(comp):
            r.append("%s deployment needs self-hostable components without a mandatory SaaS dependency" % dep.replace("_", "-"))
        if dep == "self_hosted_cloud_vm" and not comp.get("self_hostable", False):
            r.append("self-hosted VM deployment needs a self-hostable component")
        if pref == "none" and not _self_contained(comp):
            r.append("cloud_preference none means self-managed components only")
    if layer == "hosting":
        env = attrs.get("environment")
        if comp["id"] == "none_host" and ("web" in p["client_platforms"] or p["static_candidate"] or p["api_only"]):
            r.append("a web client or API needs somewhere to be served from")
        cap = attrs.get("max_egress_gb")
        if cap and p["egress_gb"] > cap:
            r.append("expected traffic (about %.0f GB/month) exceeds the %d GB/month soft limit" % (p["egress_gb"], cap))
        if dep == "managed_cloud" and env == "onprem":
            r.append("managed cloud deployment excludes on-premise hosting")
        if restricted and env != "onprem":
            r.append("%s deployment allows only on-premise hosting" % dep.replace("_", "-"))
        if dep == "self_hosted_cloud_vm" and env != "vm":
            r.append("self-hosted VM deployment needs a virtual machine host")
        if pref in CLOUDS and attrs.get("provider") != pref and comp["id"] != "none_host":
            r.append("cloud_preference is %s and this runs on %s" % (pref, attrs.get("provider")))
        if pref == "none" and env == "cloud":
            r.append("cloud_preference none means self-managed hosting only")
        if not attrs.get("static_only") and not region_ok(comp.get("regions", ["*"]), res):
            r.append("no %s region available" % res)
        if env != "onprem":
            miss = _certs_missing([c for c in HARD_CERTS if c in p["compliance"]], comp.get("certs", []))
            if miss and not attrs.get("static_only"):
                r.append("no %s attestation" % "/".join(m.upper() for m in miss))
    if layer == "database" and not is_none(comp):
        if not (comp.get("self_hostable") or "*" in comp.get("regions", []) or region_ok(comp.get("regions", []), res)):
            r.append("no %s region available" % res)
        miss = _certs_missing([c for c in HARD_CERTS if c in p["compliance"]], comp.get("certs", []))
        if miss:
            r.append("no %s attestation" % "/".join(m.upper() for m in miss))
    if layer == "backend" and attrs.get("runtime") == "baas":
        if not region_ok(comp.get("regions", ["*"]), res):
            r.append("no %s region available" % res)
        miss = _certs_missing([c for c in HARD_CERTS if c in p["compliance"]], comp.get("certs", []))
        if miss:
            r.append("no %s attestation" % "/".join(m.upper() for m in miss))
        if pref in CLOUDS and attrs.get("provider") != pref:
            r.append("cloud_preference is %s and this runs on %s" % (pref, attrs.get("provider")))
    return r


def filter_layer(layer, comps, p):
    """Split components into (kept, eliminated) where eliminated is a list of {id, name, reasons}."""
    kept, out = [], []
    for c in comps:
        reasons = component_reasons(layer, c, p)
        if reasons:
            out.append({"id": c["id"], "name": c["name"], "reasons": reasons})
        else:
            kept.append(c)
    return kept, out


def runtime_ok(be, host):
    return be["attrs"]["runtime"] in host["attrs"]["runtimes"]


def db_mode(db, be, host, p=None):
    """How the database is reached on this host: none, baas_managed, managed_native, managed_external,
    self_hosted, or None when the pairing cannot work."""
    if db["id"] == "none_db":
        return "none"
    if be["attrs"]["runtime"] == "baas" and db["id"] in be["requires"].get("database", []):
        return "baas_managed"
    acc = db["attrs"]["access"]
    own = p is not None and (p["deployment"] in ("on_premise", "air_gapped", "self_hosted_cloud_vm") or p["cloud_pref"] == "none")
    if own and acc.get("self_host") and host["attrs"]["environment"] in ("vm", "onprem"):
        return "self_hosted"
    if acc["managed"].get(host["attrs"]["provider"]):
        return "managed_native"
    if acc["managed"].get("any"):
        return "managed_external"
    sh = acc.get("self_host")
    if sh:
        if sh == "persistent_disk":
            return "self_hosted" if host["attrs"]["persistent_disk"] else None
        if host["attrs"]["environment"] in ("vm", "onprem") or host["attrs"]["persistent_disk"]:
            return "self_hosted"
    return None


def stack_reasons(fe_parts, be, db, host, p):
    """Reasons a complete combination is invalid; empty list means compatible and compliant."""
    r = []
    if not runtime_ok(be, host):
        r.append("%s cannot run on %s" % (be["name"], host["name"]))
        return r
    for part in fe_parts:
        req = part["requires"].get("backend")
        if req and be["id"] not in req:
            r.append("%s works only with %s" % (part["name"], ", ".join(req)))
    hreq = host["requires"].get("backend")
    if hreq and be["id"] not in hreq:
        r.append("%s works only with %s" % (host["name"], ", ".join(hreq)))
    breq = be["requires"].get("frontend")
    if breq and not any(part["id"] in breq for part in fe_parts):
        r.append("%s is part of %s only" % (be["name"], ", ".join(breq)))
    dreq = db["requires"].get("backend")
    if dreq and be["id"] not in dreq:
        r.append("%s cannot be used with %s" % (db["name"], be["name"]))
    breq_db = be["requires"].get("database")
    if breq_db and db["id"] not in breq_db:
        r.append("%s needs %s" % (be["name"], " or ".join(breq_db)))
    if (be["attrs"].get("runtime") == "baas" and not is_none(host) and "web" not in p["client_platforms"]
            and not p["api_only"] and not host["attrs"].get("persistent_disk")
            and not set(host["attrs"].get("runtimes", [])) - {"none", "baas", "functions"}):
        r.append("%s serves the apps directly and no web client is built, so %s would host nothing" % (be["name"], host["name"]))
    if r:
        return r
    mode = db_mode(db, be, host, p)
    if mode is None:
        r.append("%s cannot be hosted or reached from %s" % (db["name"], host["name"]))
        return r
    dep, pref, res = p["deployment"], p["cloud_pref"], p["residency"]
    if dep in ("on_premise", "air_gapped", "self_hosted_cloud_vm") and mode not in ("self_hosted", "none"):
        r.append("%s deployment needs the database to run on your own host" % dep.replace("_", "-"))
    if pref == "none" and mode not in ("self_hosted", "none"):
        r.append("cloud_preference none needs a self-managed database")
    if pref in CLOUDS:
        if mode == "managed_external" and pref not in db["attrs"].get("runs_on", []):
            r.append("%s does not run on %s" % (db["name"], pref.upper()))
        if mode == "managed_native" and host["attrs"]["provider"] != pref:
            r.append("database is not on %s" % pref.upper())
    if mode in ("managed_native", "managed_external", "self_hosted") and not host["attrs"]["static_only"]:
        if mode != "self_hosted" and not region_ok(db["regions"], res):
            r.append("%s has no %s region" % (db["name"], res))
        elif mode == "managed_external":
            # a vendor service can cover a residency on one cloud but not another
            on = db["attrs"].get("regions_by_cloud", {}).get(host["attrs"]["provider"])
            if on is not None and not region_ok(on, res):
                r.append("%s has no %s region on %s" % (db["name"], res, host["attrs"]["provider"].upper()))
        if mode == "self_hosted" and not region_ok(host["regions"], res):
            r.append("no %s region on %s" % (res, host["name"]))
    hard = [c for c in HARD_CERTS if c in p["compliance"]]
    if hard and mode in ("managed_native", "managed_external") and host["attrs"]["environment"] != "onprem":
        miss = _certs_missing(hard, db["certs"])
        if miss:
            r.append("%s has no %s attestation" % (db["name"], "/".join(m.upper() for m in miss)))
    return r


def platform_cover(fe_parts, platforms):
    """Quality 0-1 per client platform: best part. Returns dict platform -> quality."""
    out = {}
    for pl in platforms:
        out[pl] = max([part["attrs"]["platform_quality"].get(pl, 0.0) for part in fe_parts] or [0.0])
    return out
