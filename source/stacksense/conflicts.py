"""Input-level conflicts and compliance notes: contradictions or tensions in the scenario itself,
found before any stack is chosen. Each warning is {code, severity, message, fields}."""
from . import kb
from .derive import build_profile
from .cost import fmt_money


def _w(code, severity, message, fields):
    return {"code": code, "severity": severity, "message": message, "fields": list(fields)}


def detect(n):
    """Return the list of warnings for a normalised scenario (the validator's own warnings included)."""
    p = build_profile(n)
    out = list(n.get("_warnings", []))
    comp, req, infra = set(p["compliance"]), n["requirements"], n["infrastructure"]
    dep, pref, scale = p["deployment"], p["cloud_pref"], p["scale_need"]

    floor = 4.0 * scale * scale if scale > 3.0 else 0.0
    if p["budget"] < floor:
        sev = "critical" if p["budget"] < floor / 3.0 else "warning"
        out.append(_w("BUDGET_TOO_LOW_FOR_SCALE", sev,
                      "The load estimate (peak about %.0f requests/s, %s users in 12 months) usually needs at least %s per month of infrastructure; the budget is %s."
                      % (p["peak_rps"], format(p["users_12m"], ","), fmt_money(floor), fmt_money(p["budget"])),
                      ["budget.monthly_infra_usd", "scale.peak_concurrent_users"]))
    if p["avail_level"] >= 3.3 and not p["has_devops"] and p["team_size"] <= 3:
        out.append(_w("AVAILABILITY_VS_TEAM", "warning",
                      "%s%% availability needs redundancy and on-call coverage; a team of %d without DevOps will struggle, so managed services are strongly preferred."
                      % (p["availability"], p["team_size"]), ["requirements.availability", "team.has_devops", "team.size"]))
    if p["avail_level"] >= 3.3 and p["budget"] < 300:
        out.append(_w("HA_VS_BUDGET", "warning", "%s%% availability needs redundant components, which a monthly budget of %s rarely covers."
                      % (p["availability"], fmt_money(p["budget"])), ["requirements.availability", "budget.monthly_infra_usd"]))
    if p["effort_ratio"] > 1.25 and p["effort_pw"] - p["capacity_pw"] >= 2.0:
        sev = "critical" if p["effort_ratio"] > 1.6 else "warning"
        out.append(_w("TIMELINE_VS_PLATFORMS", sev,
                      "Estimated build effort is about %.0f person-weeks against roughly %.0f available in %d weeks; reduce scope, %slean on managed services."
                      % (p["effort_pw"], p["capacity_pw"], p["mvp_weeks"], "drop a platform, or " if len(p["client_platforms"]) > 1 else "add people, or "),
                      ["timeline.mvp_weeks", "team.size"] + (["project.platforms"] if len(p["client_platforms"]) > 1 else [])))
    if dep in ("on_premise", "air_gapped") and pref in ("aws", "gcp", "azure"):
        out.append(_w("DEPLOYMENT_CLOUD_CONFLICT", "critical",
                      "%s deployment cannot run on %s; the cloud preference is ignored and an on-premise host is used." % (dep.replace("_", "-"), pref.upper()),
                      ["infrastructure.deployment_model", "infrastructure.cloud_preference"]))
    if infra["data_residency"] == "other":
        out.append(_w("RESIDENCY_UNVERIFIED", "warning",
                      "Data residency 'other' does not name a region, so the engine cannot verify where records, backups, logs and sign-in data are stored; treat residency as unconfirmed, not met. Pin the hosting and database regions to the legal boundary and check that auth, email and monitoring services can stay inside it.",
                      ["infrastructure.data_residency"]))
    if dep == "air_gapped":
        needs = [x for x, on in (("payments", req["payments"]), ("SMS one-time passwords", req["auth"] == "phone_otp"),
                                 ("hosted ML APIs", req["ml_features"] in ("inference_api", "llm_rag"))) if on]
        if needs:
            out.append(_w("AIRGAPPED_SAAS_REQUIREMENT", "critical",
                          "An air-gapped deployment cannot reach external providers, yet the scenario needs %s. Those parts need a controlled gateway or a different deployment model." % ", ".join(needs),
                          ["infrastructure.deployment_model"] + (["requirements.payments"] if req["payments"] else []) + (["requirements.auth"] if req["auth"] == "phone_otp" else [])))
    if dep in ("on_premise", "air_gapped") and comp & {"hipaa", "pci_dss"}:
        out.append(_w("COMPLIANCE_SELF_ATTEST", "warning",
                      "On-premise hosting has no provider attestation for %s; you carry the audit evidence yourself." % "/".join(sorted(c.upper() for c in comp & {"hipaa", "pci_dss"})),
                      ["compliance", "infrastructure.deployment_model"]))
    texts = list(infra["existing_systems"]) + [n["project"]["description"]]
    seen = set()
    for d in kb.load()["deprecated"]:
        for t in texts:
            if d["id"] not in seen and d["_re"].search(t):
                seen.add(d["id"])
                where = "infrastructure.existing_systems" if t in infra["existing_systems"] else "project.description"
                aging = d.get("severity") == "info"
                out.append(_w("AGING_EXISTING_SYSTEM" if aging else "EOL_EXISTING_SYSTEM", "info" if aging else "warning",
                              "%s: %s" % (d["label"], d["note"]), [where]))
    if p["budget"] <= 25 and (scale >= 3.0 or p["traffic_pattern"] == "spiky"):
        out.append(_w("FREE_TIER_LIMITS", "info", "A budget of %s relies on free tiers; check bandwidth, request and storage limits during peaks." % fmt_money(p["budget"]),
                      ["budget.monthly_infra_usd", "scale.traffic_pattern"]))
    if p["static_candidate"] and p["budget"] > 200:
        out.append(_w("OVERSCALE_FOR_STATIC", "info", "A content site without accounts runs on static hosting for close to nothing; most of the budget can stay unspent.",
                      ["budget.monthly_infra_usd", "project.type"]))
    if req["payments"]:
        if "pci_dss" in comp:
            out.append(_w("PCI_SCOPE", "warning", "Use a hosted checkout or tokenisation from a PCI DSS Level 1 provider so card numbers never touch your servers; otherwise the whole stack is in PCI scope.",
                          ["requirements.payments", "compliance"]))
        else:
            out.append(_w("PCI_SCOPE", "info", "Use a hosted checkout from a PCI DSS certified provider so card data never reaches your servers.", ["requirements.payments"]))
        if req["auth"] == "none":
            out.append(_w("PAYMENTS_WITHOUT_AUTH", "warning", "Payments are required but users do not sign in; orders and refunds cannot be tied to an account.", ["requirements.payments", "requirements.auth"]))
    if "hipaa" in comp:
        out.append(_w("HIPAA_BAA_NEEDED", "warning", "HIPAA needs a signed business associate agreement with the host, database and every vendor that touches health data.", ["compliance"]))
    if "gdpr" in comp:
        out.append(_w("GDPR_NOTES", "info", "GDPR: keep a record of processing, sign a data processing agreement with every vendor that handles personal data, support access and erasure requests, and use standard contractual clauses for any transfer outside the EU/EEA%s."
                      % ("" if infra["data_residency"] == "eu" else "; consider EU data residency to keep transfers simple"),
                      ["compliance", "infrastructure.data_residency"]))
    for cert, label in (("soc2", "SOC 2"), ("iso27001", "ISO 27001")):
        if cert in comp:
            out.append(_w(cert.upper() + "_NOTES", "info",
                          "%s audits your own controls (access reviews, change management, logging, incident response); vendors with their own %s report shorten the audit, and gaps in the chosen stack are listed separately." % (label, label),
                          ["compliance"]))
    if req["auth"] == "phone_otp" and (p["india_ctx"] or infra["data_residency"] == "india"):
        out.append(_w("SMS_DLT_REQUIRED", "info", "SMS to Indian numbers needs DLT registration (entity, sender ID and message templates) with a telecom operator before launch; allow one to two weeks.",
                      ["requirements.auth"]))
    if "dpdp" in comp:
        out.append(_w("DPDP_NOTES", "info", "DPDP Act: record consent, support erasure and correction, notify breaches, and take extra care with children's data. Cross-border transfer is allowed except to countries the government restricts.",
                      ["compliance", "infrastructure.data_residency"]))
    if "rbi_localisation" in comp and infra["data_residency"] not in ("india", "none"):
        out.append(_w("RBI_RESIDENCY_CONFLICT", "critical",
                      "RBI localisation requires payment data to be stored in India, but the data residency is '%s'. Set the residency to india or drop the RBI requirement." % infra["data_residency"],
                      ["compliance", "infrastructure.data_residency"]))
    if "rbi_localisation" in comp:
        out.append(_w("RBI_LOCALISATION", "info", "RBI localisation: payment data must be stored only in India, so database, backups and logs are kept in Indian regions.", ["compliance", "infrastructure.data_residency"]))
    if infra["data_residency"] == "india" or comp & {"dpdp", "rbi_localisation"}:
        out.append(_w("CERT_IN_LOGS", "info",
                      "CERT-In directions (April 2022) apply to services used in India: enable logs on all systems, keep them for 180 days within India, sync clocks to an NTP source and report cyber incidents within 6 hours. Size log retention and storage for this.",
                      ["infrastructure.data_residency", "compliance"]))
    sens = (n.get("extra") or {}).get("data_sensitivity")
    if sens == "health" and not comp & {"hipaa", "dpdp", "gdpr"}:
        out.append(_w("SENSITIVE_DATA_UNREGULATED", "warning",
                      "The data is marked as health data but no health or privacy regime (HIPAA, DPDP, GDPR) is listed: check which applies to your patients, and plan for encryption at rest, access audit logs and a vendor agreement with each processor.",
                      ["extra.data_sensitivity", "compliance"]))
    elif sens == "financial" and not comp & {"pci_dss", "rbi_localisation"}:
        out.append(_w("SENSITIVE_DATA_UNREGULATED", "info",
                      "The data is marked as financial but no financial regime (PCI DSS, RBI localisation) is listed: keep card data with a tokenising processor so it stays outside PCI DSS scope, encrypt account data at rest and log access to it.",
                      ["extra.data_sensitivity", "compliance"]))
    hints = p["hints"]
    if hints["low_end_devices"] and not p["api_only"]:
        out.append(_w("LOW_END_DEVICES", "warning",
                      "The description mentions low-end phones: keep the app bundle and screen count small, avoid heavy web views and animations, and test release builds on a 2 GB RAM device.",
                      ["project.description", "project.platforms"]))
    if hints["poor_connectivity"] and not p["api_only"]:
        media = " Resize and compress photos on the device (about 1280 px on the long edge) and upload them in resumable chunks." if req["file_media_storage"] else ""
        local = "save every write locally first and sync in the background with retries" if p["offline"] else "turn on offline support or at least save drafts locally, since requests will fail mid-way"
        out.append(_w("WEAK_NETWORK", "warning",
                      "The description mentions weak or intermittent networks: %s, use client-generated ids so a retried upload is not stored twice, and keep payloads small.%s" % (local, media),
                      ["project.description", "requirements.offline_support"] + (["requirements.file_media_storage"] if media else [])))
    if p["offline"] and not p["api_only"] and (p["workload"] == "write_heavy" or p["realtime"] or p["multi_tenant"]):
        out.append(_w("OFFLINE_CONFLICTS", "info",
                      "Offline edits made on several devices will collide when they sync; decide the rule per record type (server wins, last write wins per field, or keep both for review) before building the sync layer.",
                      ["requirements.offline_support", "scale.workload"]))
    if hints["location"]:
        law = "/".join(x.upper() for x in sorted(comp & {"dpdp", "gdpr", "hipaa"})) or "privacy law"
        out.append(_w("LOCATION_DATA", "info",
                      "GPS or location data is personal data under %s: record consent with each point, keep only the precision you need, and set a retention period." % law,
                      ["project.description", "compliance"]))
    if p["realtime"] and p["p95"] is not None and p["p95"] <= 150:
        spread = p["geography"] in ("multi_region", "global")
        out.append(_w("REALTIME_LATENCY", "warning" if (spread or p["p95"] <= 100 and p["peak_concurrent"] >= 1000) else "info",
                      "A p95 of %d ms with live updates%s is reachable only over persistent connections (WebSockets) to servers in the users' region; avoid cold-starting functions on the hot path and load-test the fan-out at the expected %s concurrent users."
                      % (p["p95"], " for users spread across regions" if spread else "", format(p["peak_concurrent"], ",")),
                      ["requirements.p95_latency_ms", "requirements.realtime", "scale.peak_concurrent_users"]))
    if p["offline"] and p["api_only"]:
        out.append(_w("OFFLINE_WITH_API_ONLY", "warning", "Offline support is a client feature, but the project is API-only; it applies to whoever builds the client.", ["requirements.offline_support", "project.platforms"]))
    if p["seo"] and not p["api_only"] and "web" not in p["client_platforms"]:
        out.append(_w("SEO_WITHOUT_WEB", "warning", "SEO matters for web pages, but web is not a target platform.", ["requirements.seo_critical", "project.platforms"]))
    if p["multi_tenant"] and req["auth"] == "none":
        out.append(_w("TENANTS_WITHOUT_AUTH", "warning", "Multi-tenancy needs a way to identify each tenant's users, but no authentication is requested.", ["requirements.multi_tenant", "requirements.auth"]))
    return out
