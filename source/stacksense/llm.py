"""Optional Gemini helper (plain REST). Never takes part in choosing the stack.

Two jobs only: suggest structured fields from a free-text description, and
reword explanations the engine already produced. Every call goes through
_post(), and every public function checks enabled() first, so with
STACKSENSE_LLM=off (or the per-request flag set to false) nothing touches the
network. Any failure returns None and the caller falls back to rules only.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from .extract import FIELD_SPEC

TIMEOUT = 20
RETRIES = 2
_OFF = {"off", "0", "false", "no", "disabled"}
_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _key():
    return os.environ.get("GEMINI_API_KEY", "").strip()


def env_allows():
    return os.environ.get("STACKSENSE_LLM", "").strip().lower() not in _OFF


def available():
    return bool(_key())


def enabled(request_flag=None):
    """True only when the env switch allows it, a key exists and the request did not opt out."""
    if request_flag is False:
        return False
    return env_allows() and available()


def _post(payload):
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
    req = urllib.request.Request(
        _URL.format(model=model), data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": _key()})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _generate(prompt, request_flag=None):
    if not enabled(request_flag):
        return None
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    for attempt in range(RETRIES):
        try:
            data = _post(payload)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                return None
        except (urllib.error.URLError, OSError):
            pass
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        time.sleep(1.5 * (attempt + 1))
    return None


def _clean(path, value):
    """Return a value of the right type for the path, or None to drop it."""
    spec = FIELD_SPEC.get(path)
    if spec is None:
        return None
    kind, allowed = spec
    if kind == "enum":
        v = str(value).strip().lower()
        return v if v in allowed else None
    if kind == "bool":
        return value if isinstance(value, bool) else None
    if kind in ("int", "num"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return None
        return int(value) if kind == "int" else value
    if kind == "list":
        if not isinstance(value, list):
            return None
        out = []
        for v in value:
            v = str(v).strip()
            if allowed is not None:
                v = v.lower()
                if v not in allowed:
                    continue
            if v and v not in out:
                out.append(v)
        return out or None
    if kind == "str":
        return str(value).strip()[:80] or None
    return None


def suggest(text, request_flag=None):
    """Ask the model for field suggestions; returns a validated list or None."""
    if not enabled(request_flag) or not text.strip():
        return None
    paths = ", ".join("%s (%s%s)" % (p, k, ": " + "|".join(a) if a else "") for p, (k, a) in FIELD_SPEC.items())
    prompt = (
        "Read this project description and list requirement fields it states or clearly implies. "
        "Reply with a JSON array of objects {\"path\": ..., \"value\": ..., \"evidence\": exact short quote, "
        "\"confidence\": 0..1}. Use only these paths and value types: " + paths + ". "
        "Omit anything not supported by the text. Use users_at_launch only when the text gives a launch-time figure; a single 'expecting N users' figure is users_12_months. Do not recommend technologies.\n\nDESCRIPTION:\n" + text[:6000])
    data = _generate(prompt, request_flag)
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        value = _clean(path, item.get("value"))
        if value is None:
            continue
        try:
            conf = min(0.7, max(0.2, float(item.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        out.append({"path": path, "value": value, "confidence": round(conf, 2),
                    "evidence": str(item.get("evidence", ""))[:200], "source": "gemini"})
    return out


# Names the narrative must not introduce unless the result already uses them.
_KNOWN = ["react", "next.js", "vue", "nuxt", "angular", "svelte", "astro", "flutter", "react native", "django", "fastapi",
          "flask", "express", "nestjs", "spring boot", "asp.net", "laravel", "rails", "go", "gin", "fiber", "postgresql",
          "postgres", "mysql", "mongodb", "sqlite", "dynamodb", "firestore", "redis", "kafka", "rabbitmq", "aws", "gcp",
          "azure", "vercel", "netlify", "cloudflare", "heroku", "render", "fly.io", "railway", "digitalocean", "hetzner",
          "kubernetes", "firebase", "supabase", "stripe", "razorpay", "auth0", "clerk", "keycloak", "cognito",
          "elasticsearch", "opensearch", "meilisearch", "typesense", "algolia", "docker", "terraform", "oracle",
          "sql server", "mariadb", "cockroachdb", "clickhouse", "snowflake", "bigquery"]


def _names_in(text):
    low = text.lower()
    return {n for n in _KNOWN if re.search(r"(?<![\w.])" + re.escape(n) + r"(?![\w.])", low)}


def _result_names(result):
    stack = result.get("stack") or {}
    texts = []
    comps = [stack.get(k) for k in ("frontend", "backend", "database", "hosting")] + list(stack.get("supporting") or [])
    for c in comps:
        if not isinstance(c, dict):
            continue
        texts.append(str(c.get("choice", "")))
        for a in c.get("alternatives") or []:
            texts.append(str(a.get("choice", "")))
    texts.append(json.dumps(result.get("assumptions", [])))
    texts.append(json.dumps(result.get("warnings", [])))
    texts.append(str(result.get("summary", "")))
    return _names_in(" ".join(texts))


def narrate(result, request_flag=None):
    """Reword the summary only. Returns {text, model_note} or None; never alters the result."""
    if not enabled(request_flag) or not isinstance(result, dict) or not result.get("stack"):
        return None
    brief = {"summary": result.get("summary"), "architecture_style": result.get("architecture_style"),
             "components": {k: {"choice": (result["stack"].get(k) or {}).get("choice"),
                                "justification": (result["stack"].get(k) or {}).get("justification")}
                            for k in ("frontend", "backend", "database", "hosting")}}
    prompt = ("Rewrite this already-decided recommendation as two short plain-English paragraphs for a non-expert. "
              "Do not change, add or rename any technology. Reply as JSON {\"text\": string}.\n" + json.dumps(brief))
    data = _generate(prompt, request_flag)
    if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not data["text"].strip():
        return None
    text = data["text"].strip()
    if _names_in(text) - _result_names(result):
        return None
    return {"text": text[:2500]}
