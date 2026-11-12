"""FastAPI app: JSON API for the engine plus the static single-page UI."""
import importlib
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import extract, llm

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
SCENARIO_DIR = ROOT / "scenarios"
MAX_UPLOAD = 8 * 1024 * 1024
ENGINE_VERSION = "1.0.0"

app = FastAPI(title="ArchLens", version=ENGINE_VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json")


def _engine(module, attr):
    try:
        return getattr(importlib.import_module("stacksense." + module), attr)
    except (ImportError, AttributeError):
        raise HTTPException(503, "The recommendation engine is not available in this build.")


async def _json_body(request):
    try:
        return await request.json()
    except ValueError:
        raise HTTPException(400, "Request body must be valid JSON.")


def _unwrap(body, key):
    """Accept either {key: payload, ...} or the payload itself."""
    if isinstance(body, dict) and key in body and isinstance(body[key], dict) and "project" not in body:
        return body[key], body.get("options") or {}
    return body, {}


def _llm_flag(options):
    v = options.get("llm") if isinstance(options, dict) else None
    return v if isinstance(v, bool) else None


@app.get("/api/meta")
def meta():
    enums = {p: spec[1] for p, spec in extract.FIELD_SPEC.items() if spec[0] in ("enum", "list") and spec[1]}
    return {"engine_version": ENGINE_VERSION, "enums": enums,
            "llm_available": llm.available(), "llm_enabled": llm.enabled(),
            "llm_env_off": not llm.env_allows(), "inr_per_usd": extract.INR_PER_USD}


@app.get("/api/scenarios")
def scenarios():
    out = []
    for p in sorted(SCENARIO_DIR.glob("P*.json")) if SCENARIO_DIR.is_dir() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"id": data.get("scenario_id", p.stem), "name": (data.get("project") or {}).get("name", p.stem),
                    "type": (data.get("project") or {}).get("type", ""), "scenario": data})
    return out


def _merge_llm(result, text, flag):
    extra = llm.suggest(text, flag)
    if extra is None:
        return False
    have = {(s["path"], json.dumps(s["value"], sort_keys=True)) for s in result["suggestions"]}
    for s in extra:
        if (s["path"], json.dumps(s["value"], sort_keys=True)) not in have:
            result["suggestions"].append(s)
    result["fields"] = extract.build_fields(result["suggestions"], min_conf=0.4)
    return True


@app.post("/api/extract")
async def api_extract(request: Request):
    ctype = request.headers.get("content-type", "")
    text, filename, data, flag = "", None, None, None
    if ctype.startswith("multipart/"):
        form = await request.form()
        up = form.get("file")
        if up is not None and hasattr(up, "read"):
            data = await up.read()
            filename = up.filename
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "File is larger than 8 MB.")
        text = form.get("text") or ""
        if str(form.get("llm", "")).lower() in ("false", "0", "off"):
            flag = False
    else:
        body = await _json_body(request)
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected an object with a text field.")
        text = body.get("text") or ""
        flag = body.get("llm") if isinstance(body.get("llm"), bool) else None
    if data is not None:
        try:
            result = extract.extract_document(filename, data)
        except Exception as exc:
            raise HTTPException(422, "Could not read %s: %s" % (filename or "file", type(exc).__name__))
        text = result["text"]
    elif isinstance(text, str) and text.strip():
        embedded = extract.find_embedded_scenario(text)
        result = extract.extract_text(text)
        result["scenario"] = embedded
        if embedded is not None:
            result["fields"] = embedded
    else:
        raise HTTPException(400, "Provide a file or some text.")
    used = False
    if result.get("scenario") is None:
        used = _merge_llm(result, text, flag)
    result["used_llm"] = used
    result["llm_enabled"] = llm.enabled(flag)
    result["file_name"] = filename
    return result


@app.post("/api/validate")
async def api_validate(request: Request):
    scenario, _ = _unwrap(await _json_body(request), "scenario")
    validate = _engine("validate", "validate")
    normalized, errors, assumptions = validate(scenario)
    warnings = []
    if not errors:
        warnings = _engine("conflicts", "detect")(normalized)
    return {"normalized": normalized, "validation_errors": errors, "assumptions": assumptions,
            "warnings": warnings, "valid": not errors}


@app.post("/api/recommend")
async def api_recommend(request: Request):
    scenario, options = _unwrap(await _json_body(request), "scenario")
    result = _engine("engine", "recommend")(scenario)
    return JSONResponse(result)


@app.post("/api/narrate")
async def api_narrate(request: Request):
    result, options = _unwrap(await _json_body(request), "result")
    flag = _llm_flag(options)
    if not isinstance(result, dict) or not llm.enabled(flag):
        return {"text": None, "notice": "AI wording is off; showing engine text only."}
    ai = llm.narrate(result, flag)
    return ai if ai else {"text": None, "notice": "Model text unavailable; showing engine text only."}


@app.post("/api/report.pdf")
async def api_report(request: Request):
    result, _ = _unwrap(await _json_body(request), "result")
    if not isinstance(result, dict):
        raise HTTPException(400, "Expected a result object.")
    from ..report import build_pdf
    pdf = build_pdf(result)
    name = "archlens_%s.pdf" % (str(result.get("scenario_id") or "report").replace("/", "_")[:40])
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="%s"' % name})


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR)), name="static")
