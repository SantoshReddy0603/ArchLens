import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from stacksense import llm
from stacksense.web import app as webapp

client = TestClient(webapp.app)

SCENARIO = {
    "scenario_id": "T1",
    "project": {"name": "Demo", "type": "web_app", "platforms": ["web"]},
    "scale": {"users_at_launch": 100, "users_12_months": 1000},
    "team": {"size": 2, "experience": "intermediate", "languages": ["python"]},
    "budget": {"monthly_infra_usd": 50},
    "timeline": {"mvp_weeks": 8},
}
DESC = "Chat app for 5k users, team of 3 knowing python, budget $100/month, MVP in 6 weeks."


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted")
    monkeypatch.setattr(llm, "_post", boom)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    return boom


def engine_ready():
    try:
        webapp._engine("engine", "recommend")
        webapp._engine("validate", "validate")
        return True
    except Exception:
        return False


needs_engine = pytest.mark.skipif(not engine_ready(), reason="engine not available")


def test_static_ui_and_assets():
    r = client.get("/")
    assert r.status_code == 200 and "ArchLens" in r.text
    assert client.get("/favicon.svg").status_code == 200
    assert client.get("/fonts/instrument-serif-latin-400-normal.woff2").status_code == 200
    assert client.get("/js/app.js").status_code == 200


def test_meta(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    m = client.get("/api/meta").json()
    assert "web" in m["enums"]["project.platforms"]
    assert m["llm_available"] is False and m["llm_enabled"] is False


def test_meta_env_off_wins_over_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("STACKSENSE_LLM", "off")
    m = client.get("/api/meta").json()
    assert m["llm_available"] is True and m["llm_enabled"] is False


def test_scenarios_endpoint_shape():
    data = client.get("/api/scenarios").json()
    assert isinstance(data, list)
    for s in data:
        assert {"id", "name", "scenario"} <= set(s)


def test_extract_text_json_body(no_network, monkeypatch):
    monkeypatch.setenv("STACKSENSE_LLM", "off")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    r = client.post("/api/extract", json={"text": DESC}).json()
    assert r["fields"]["team"]["size"] == 3
    assert r["used_llm"] is False and r["suggestions"]


def test_extract_upload_txt_and_json(no_network, monkeypatch):
    monkeypatch.setenv("STACKSENSE_LLM", "off")
    r = client.post("/api/extract", files={"file": ("b.txt", DESC.encode())}).json()
    assert r["fields"]["budget"]["monthly_infra_usd"] == 100
    r = client.post("/api/extract", files={"file": ("s.json", json.dumps(SCENARIO).encode())}).json()
    assert r["scenario"]["scenario_id"] == "T1"


def test_extract_bad_requests():
    assert client.post("/api/extract", json={"text": "  "}).status_code == 400
    assert client.post("/api/extract", content=b"{bad", headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/api/extract", files={"file": ("x.pdf", b"not a pdf")}).status_code == 422


def test_llm_per_request_flag_blocks_network(no_network, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("STACKSENSE_LLM", raising=False)
    r = client.post("/api/extract", json={"text": DESC, "llm": False}).json()
    assert r["used_llm"] is False


def test_llm_used_when_enabled(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("STACKSENSE_LLM", raising=False)
    reply = [{"path": "project.type", "value": "marketplace", "evidence": "x", "confidence": 0.9},
             {"path": "requirements.auth", "value": "bogus", "evidence": "x"},
             {"path": "not.a.path", "value": 1}]
    monkeypatch.setattr(llm, "_post", lambda p: {"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]})
    r = client.post("/api/extract", json={"text": DESC}).json()
    assert r["used_llm"] is True
    gem = [s for s in r["suggestions"] if s["source"] == "gemini"]
    assert [s["path"] for s in gem] == ["project.type"] and gem[0]["confidence"] <= 0.7


def test_llm_failure_falls_back(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("STACKSENSE_LLM", raising=False)
    import urllib.error

    def fail(p):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(llm, "_post", fail)
    r = client.post("/api/extract", json={"text": DESC}).json()
    assert r["used_llm"] is False and r["fields"]["team"]["size"] == 3


def test_narrate_guard_rejects_new_products(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("STACKSENSE_LLM", raising=False)
    result = {"summary": "s", "stack": {k: {"choice": c, "justification": "j", "alternatives": []}
                                        for k, c in (("frontend", "Astro"), ("backend", "None"), ("database", "None"), ("hosting", "Netlify"))}}

    def reply(text):
        return lambda p: {"candidates": [{"content": {"parts": [{"text": json.dumps({"text": text})}]}}]}
    monkeypatch.setattr(llm, "_post", reply("Astro pages are served from Netlify."))
    assert llm.narrate(result)["text"].startswith("Astro")
    monkeypatch.setattr(llm, "_post", reply("Use MongoDB for storage."))
    assert llm.narrate(result) is None
    assert llm.narrate(result, request_flag=False) is None


@needs_engine
def test_validate_and_recommend_off_no_network(no_network, monkeypatch):
    monkeypatch.setenv("STACKSENSE_LLM", "off")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    v = client.post("/api/validate", json=SCENARIO).json()
    assert v["valid"] is True and isinstance(v["warnings"], list)
    bad = client.post("/api/validate", json={"project": {}}).json()
    assert bad["valid"] is False and bad["validation_errors"]
    r = client.post("/api/recommend", json={"scenario": SCENARIO, "options": {"llm": True}}).json()
    assert r["status"] in ("ok", "ok_with_warnings") and "ai_narrative" not in r


@needs_engine
def test_same_stack_llm_on_or_off(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("STACKSENSE_LLM", raising=False)
    monkeypatch.setattr(llm, "_post", lambda p: {"candidates": [{"content": {"parts": [{"text": json.dumps({"text": "Plain words."})}]}}]})
    on = client.post("/api/recommend", json={"scenario": SCENARIO}).json()
    off = client.post("/api/recommend", json={"scenario": SCENARIO, "options": {"llm": False}}).json()
    assert "ai_narrative" not in on and "ai_narrative" not in off
    assert on == off
    ai = client.post("/api/narrate", json={"result": on}).json()
    assert ai.get("text") == "Plain words."
    ai_off = client.post("/api/narrate", json={"result": on, "options": {"llm": False}}).json()
    assert ai_off.get("text") is None


@needs_engine
def test_invalid_input_recommend_and_pdf():
    r = client.post("/api/recommend", json={"scenario": {"project": {}}}).json()
    assert r["status"] == "invalid_input" and r["stack"] is None
    pdf = client.post("/api/report.pdf", json=r)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


@needs_engine
def test_report_pdf_for_real_result():
    r = client.post("/api/recommend", json={"scenario": SCENARIO, "options": {"llm": False}}).json()
    pdf = client.post("/api/report.pdf", json={"result": r})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert len(pdf.content) > 2000
    import io
    from pypdf import PdfReader
    text = " ".join(pg.extract_text() for pg in PdfReader(io.BytesIO(pdf.content)).pages)
    for part in ("Ranking of the top stacks", "Score breakdown", "Layer weights", "Eliminated candidates", "Stack search"):
        assert part in text


def test_report_handles_odd_results():
    from stacksense.report import build_pdf
    assert build_pdf({}).startswith(b"%PDF")
    assert build_pdf({"status": "infeasible", "stack": None, "summary": "x ₹ →",
                      "warnings": [{"code": "A", "severity": "critical", "message": "m <b>"}]}).startswith(b"%PDF")


def test_report_pdf_endpoint_rejects_non_object():
    assert client.post("/api/report.pdf", json=[1, 2]).status_code == 400
