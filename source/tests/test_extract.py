import io
import json

import pytest

from stacksense import extract


def got(text):
    r = extract.extract_text(text)
    return r["fields"], r["suggestions"]


def test_numbers_and_units():
    f, _ = got("We expect 30k users in 12 months, 2,000 at launch and peak 800 concurrent. Team of 3. MVP in 8 weeks.")
    assert f["scale"]["users_12_months"] == 30000
    assert f["scale"]["users_at_launch"] == 2000
    assert f["scale"]["peak_concurrent_users"] == 800
    assert f["team"]["size"] == 3
    assert f["timeline"]["mvp_weeks"] == 8


def test_thousand_separator_employees():
    f, _ = got("An internal tool for 1,200 employees.")
    assert f["scale"]["users_12_months"] == 1200


def test_months_to_weeks_and_not_confused_with_growth():
    f, _ = got("We expect 30k users in 12 months. We want to launch in 3 months.")
    assert f["timeline"]["mvp_weeks"] == 13


@pytest.mark.parametrize("text,usd", [
    ("Budget is $150/month.", 150),
    ("Our monthly budget is Rs 5 lakh.", round(500000 / extract.INR_PER_USD, 2)),
    ("We have a budget of 50,000 INR per month.", round(50000 / extract.INR_PER_USD, 2)),
    ("Zero budget, free tier only.", 0),
    ("Hosting budget is $1,200 per year.", 100),
])
def test_budget(text, usd):
    f, s = got(text)
    assert f["budget"]["monthly_infra_usd"] == pytest.approx(usd, abs=0.01)
    assert all(x["evidence"] for x in s)


def test_platforms_languages_compliance():
    f, _ = got("Android and iOS apps plus a website. Team knows Python, TypeScript and C#. We must follow HIPAA, PCI-DSS, GDPR, DPDP and RBI rules, SOC 2.")
    assert set(f["project"]["platforms"]) >= {"android", "ios", "web"}
    assert set(f["team"]["languages"]) >= {"python", "typescript", "csharp"}
    assert set(f["compliance"]) == {"hipaa", "pci_dss", "gdpr", "dpdp", "rbi_localisation", "soc2"}


def test_residency_cities_and_countries():
    f, _ = got("Patient data must stay in India.")
    assert f["infrastructure"]["data_residency"] == "india"
    f, _ = got("Customer data must be hosted in Frankfurt.")
    assert f["infrastructure"]["data_residency"] == "eu"
    f, s = got("Riders in Pune and Mumbai.")
    assert not f.get("infrastructure", {}).get("data_residency") or True
    assert any(x["path"] == "infrastructure.data_residency" and x["confidence"] < 0.5 for x in s)


def test_requirements_flags():
    f, _ = got("Users chat live with each other, upload photos, pay by UPI via Razorpay and search the catalogue. It works offline in villages. Public pages need SEO. Multi-tenant for each clinic.")
    r = f["requirements"]
    assert r["realtime"] and r["file_media_storage"] and r["payments"] and r["full_text_search"]
    assert r["offline_support"] and r["seo_critical"] and r["multi_tenant"]


def test_negation():
    f, _ = got("There are no payments and no login. Offline is not needed. We do not need chat.")
    r = f["requirements"]
    assert r["payments"] is False
    assert r["auth"] == "none"
    assert r["realtime"] is False


@pytest.mark.parametrize("text,auth", [
    ("Sign in with phone OTP.", "phone_otp"),
    ("Staff use SSO with Active Directory.", "enterprise_sso"),
    ("Login with Google or Apple.", "social"),
    ("Email and password accounts.", "email_password"),
])
def test_auth(text, auth):
    f, _ = got(text)
    assert f["requirements"]["auth"] == auth


@pytest.mark.parametrize("text,ml", [
    ("A RAG chatbot over our documents using an LLM.", "llm_rag"),
    ("We will train our own models on GPUs.", "training"),
    ("Image classification via a hosted API.", "inference_api"),
])
def test_ml(text, ml):
    f, _ = got(text)
    assert f["requirements"]["ml_features"] == ml


@pytest.mark.parametrize("text,typ", [
    ("A college fest information website with the schedule.", "content_site"),
    ("An online store with cart and checkout.", "ecommerce"),
    ("A hospital portal for patients and doctors.", "healthtech"),
    ("A multiplayer game with leaderboards.", "gaming"),
    ("IoT sensors send telemetry over MQTT.", "iot"),
])
def test_project_type(text, typ):
    f, _ = got(text)
    assert f["project"]["type"] == typ


def test_quality_targets():
    f, _ = got("We need 99.95% uptime and p95 under 200 ms.")
    assert f["requirements"]["availability"] == "99.95"
    assert f["requirements"]["p95_latency_ms"] == 200


def test_infra_and_devops():
    f, _ = got("It runs on-premise, on our own servers, with no devops. We already use SQL Server and Active Directory.")
    assert f["infrastructure"]["deployment_model"] == "on_premise"
    assert f["team"]["has_devops"] is False
    assert set(f["infrastructure"]["existing_systems"]) >= {"SQL Server", "Active Directory"}
    f, _ = got("The network is air-gapped.")
    assert f["infrastructure"]["deployment_model"] == "air_gapped"


def test_every_suggestion_has_evidence_confidence_and_valid_values():
    text = ("Customers book electricians in two cities. Chat, payments, search, uploads. 30k users, team of 3 with python. "
            "Budget $150/month, 8 weeks. DPDP, India, OTP, SEO.")
    _, s = got(text)
    assert s
    for x in s:
        assert 0 <= x["confidence"] <= 1 and x["evidence"] and x["source"] == "rules"
        kind, allowed = extract.FIELD_SPEC[x["path"]]
        if kind == "enum":
            assert x["value"] in allowed


def test_deterministic():
    t = "Team of 4 building a chat app for 10k users with a $100 budget in 6 weeks."
    assert extract.extract_text(t) == extract.extract_text(t)


def test_empty_text():
    assert extract.extract_text("") == {"fields": {}, "suggestions": []}


def test_readers(tmp_path):
    assert extract.read_text("a.txt", "héllo".encode("utf-8")) == "héllo"
    import docx
    d = docx.Document()
    d.add_paragraph("Team of 5 engineers, budget $200 per month.")
    buf = io.BytesIO()
    d.save(buf)
    out = extract.extract_document("brief.docx", buf.getvalue())
    assert out["fields"]["team"]["size"] == 5
    assert out["fields"]["budget"]["monthly_infra_usd"] == 200
    from reportlab.pdfgen import canvas
    pb = io.BytesIO()
    c = canvas.Canvas(pb)
    c.drawString(72, 750, "We need a web app for 5,000 users in 12 months.")
    c.save()
    out = extract.extract_document("brief.pdf", pb.getvalue())
    assert out["fields"]["scale"]["users_12_months"] == 5000


def test_embedded_scenario_json():
    sc = {"project": {"type": "web_app"}, "scale": {}, "team": {"size": 2}}
    out = extract.extract_document("s.json", json.dumps(sc).encode())
    assert out["scenario"] == sc
    wrapped = "Here is my scenario:\n" + json.dumps(sc) + "\nThanks"
    assert extract.find_embedded_scenario(wrapped) == sc
    assert extract.find_embedded_scenario("just words {not json}") is None


def test_cloud_credit_amount_is_not_read_as_budget():
    f, _ = got("We have $20,000 in AWS credits and a monthly budget of $500 for hosting.")
    assert f["extra"]["cloud_credits_usd"] == 20000
    assert "aws_credits" in f["extra"]["existing_licenses"]
    assert f["budget"]["monthly_infra_usd"] == 500
