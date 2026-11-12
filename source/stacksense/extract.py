"""Deterministic requirement extractor.

Turns free text (pasted, or read from .txt/.pdf/.docx/.json) into suggested
scenario fields. Every suggestion carries the evidence snippet it came from and
a confidence in [0, 1]. Nothing here calls a network service; the optional LLM
layer only adds to what this module finds.
"""
import io
import json
import re

# Path -> (kind, allowed values). Used to validate suggestions from any source.
FIELD_SPEC = {
    "project.name": ("str", None),
    "project.type": ("enum", ["content_site", "web_app", "saas_b2b", "ecommerce", "marketplace", "social",
                              "realtime_collab", "fintech", "healthtech", "data_analytics", "iot",
                              "ai_ml_product", "internal_tool", "gaming", "api_platform", "other"]),
    "project.platforms": ("list", ["web", "android", "ios", "desktop", "api_only"]),
    "scale.users_at_launch": ("int", None),
    "scale.users_12_months": ("int", None),
    "scale.peak_concurrent_users": ("int", None),
    "scale.traffic_pattern": ("enum", ["steady", "spiky", "seasonal"]),
    "scale.workload": ("enum", ["read_heavy", "balanced", "write_heavy"]),
    "scale.data_volume_gb_12_months": ("num", None),
    "team.size": ("int", None),
    "team.experience": ("enum", ["beginner", "intermediate", "senior", "mixed"]),
    "team.languages": ("list", ["python", "javascript", "typescript", "java", "csharp", "php", "go", "kotlin",
                                "swift", "ruby", "rust", "dart", "sql", "cpp"]),
    "team.has_devops": ("bool", None),
    "budget.monthly_infra_usd": ("num", None),
    "timeline.mvp_weeks": ("int", None),
    "infrastructure.cloud_preference": ("enum", ["any", "aws", "gcp", "azure", "none"]),
    "infrastructure.deployment_model": ("enum", ["managed_cloud", "self_hosted_cloud_vm", "on_premise", "air_gapped"]),
    "infrastructure.data_residency": ("enum", ["none", "india", "eu", "us", "other"]),
    "infrastructure.existing_systems": ("list", None),
    "requirements.realtime": ("bool", None),
    "requirements.offline_support": ("bool", None),
    "requirements.full_text_search": ("bool", None),
    "requirements.file_media_storage": ("bool", None),
    "requirements.payments": ("bool", None),
    "requirements.auth": ("enum", ["none", "email_password", "social", "phone_otp", "enterprise_sso"]),
    "requirements.multi_tenant": ("bool", None),
    "requirements.ml_features": ("enum", ["none", "inference_api", "llm_rag", "training"]),
    "requirements.seo_critical": ("bool", None),
    "requirements.availability": ("enum", ["99", "99.9", "99.95", "99.99"]),
    "requirements.p95_latency_ms": ("int", None),
    "compliance": ("list", ["gdpr", "dpdp", "hipaa", "pci_dss", "rbi_localisation", "soc2", "iso27001"]),
    "priorities.cost": ("int", None),
    "priorities.time_to_market": ("int", None),
    "priorities.scalability": ("int", None),
    "priorities.performance": ("int", None),
    "priorities.maintainability": ("int", None),
    "priorities.security": ("int", None),
    "extra.learning_appetite": ("enum", ["low", "medium", "high"]),
    "extra.lock_in_tolerance": ("enum", ["low", "medium", "high"]),
    "extra.user_geography": ("enum", ["single_city", "country", "multi_region", "global"]),
    "extra.data_sensitivity": ("enum", ["public", "internal", "personal", "financial", "health"]),
    "extra.budget_strictness": ("enum", ["flexible", "firm", "hard"]),
    "extra.hiring_plan": ("enum", ["none", "slow", "aggressive"]),
    "extra.mobile_preference": ("enum", ["no_preference", "native", "cross_platform", "pwa"]),
    "extra.existing_licenses": ("list", ["microsoft_365", "aws_credits", "gcp_credits", "azure_credits"]),
    "extra.expected_rps": ("num", None),
    "extra.cloud_credits_usd": ("num", None),
}

INR_PER_USD = 88.0
_MULT = {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "m": 1e6,
         "mn": 1e6, "million": 1e6, "crore": 1e7, "crores": 1e7, "cr": 1e7, "b": 1e9, "billion": 1e9}
_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "a": 1, "single": 1, "solo": 1}
_NUM = r"(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?"
_MULTRE = r"(?:\s?(?:k|thousand|lakhs?|lacs?|mn|million|crores?|cr|m|billion|b)\b)?"
_NEG = re.compile(r"\b(no|not|without|never|nor|don't|doesn't|dont|won't|excluding|neither)\b[^.;\n]{0,25}$", re.I)


def _num(s, mult=None):
    v = float(s.replace(",", ""))
    if mult:
        v *= _MULT.get(mult.strip().lower(), 1)
    return v


def _split_num(raw):
    m = re.match(r"(" + _NUM + r")\s?([a-z]*)$", raw.strip(), re.I)
    if not m:
        return None
    mult = m.group(2).lower()
    if mult and mult not in _MULT:
        return None
    return _num(m.group(1), mult or None)


class _Collector:
    def __init__(self, text):
        self.text = text
        self.items = {}  # (path, repr(value)) -> suggestion
        self.order = []

    def snippet(self, start, end):
        s = max(0, start - 45)
        e = min(len(self.text), end + 55)
        out = re.sub(r"\s+", " ", self.text[s:e]).strip()
        return ("..." if s > 0 else "") + out + ("..." if e < len(self.text) else "")

    def add(self, path, value, conf, span, note=None):
        key = (path, json.dumps(value, sort_keys=True))
        ev = self.snippet(*span)
        if note:
            ev += " [" + note + "]"
        cur = self.items.get(key)
        if cur is None:
            self.items[key] = {"path": path, "value": value, "confidence": round(conf, 2),
                               "evidence": ev, "source": "rules"}
            self.order.append(key)
        elif conf > cur["confidence"]:
            cur["confidence"] = round(conf, 2)
            cur["evidence"] = ev

    def negated(self, start):
        return bool(_NEG.search(self.text[max(0, start - 35):start]))


def _sentences(text):
    for m in re.finditer(r"[^.!?\n]+(?:[.!?](?!\d)|\n|$)", text):
        yield m.group(0), m.start()


def _has(rx, s):
    return re.search(rx, s, re.I)


# ---- individual rule groups -------------------------------------------------

def _users(c):
    people = (r"(?:active\s+|monthly\s+active\s+|registered\s+|daily\s+active\s+)?(?:users|customers|students|members|"
              r"subscribers|mau|dau|accounts|employees|patients|riders|drivers|clients|visitors|players|shoppers|"
              r"merchants|sellers|providers|staff|people|devices|farmers|citizens|parents|teachers)")
    rx = re.compile(r"(?<![\w.])(" + _NUM + r")" + _MULTRE + r"\+?\s+(?:[a-z-]+\s+){0,2}?" + people + r"\b", re.I)
    found = {}
    for sent, off in _sentences(c.text):
        for m in rx.finditer(sent):
            tail = sent[m.end():m.end() + 40]
            head = sent[:m.start()]
            raw = m.group(0)
            numtxt = re.match(r"(" + _NUM + r")" + _MULTRE, raw, re.I).group(0)
            val = _split_num(numtxt)
            if val is None or val <= 0:
                continue
            val = int(val)
            span = (off + m.start(), off + m.end())
            ctx = sent
            if _has(r"\bconcurrent|simultaneous|at once|same time|at a time", raw + tail) or \
                    _has(r"\bpeak\b[^.]{0,25}$", head):
                found.setdefault("scale.peak_concurrent_users", []).append((val, 0.85, span))
            elif _has(r"\b(at launch|on launch|initially|initial|day one|day 1|beta|pilot|to start|starting with|"
                      r"first month|currently|today|right now|existing)\b", ctx) and \
                    not _has(r"12 months|a year|one year|year one|next year", ctx):
                found.setdefault("scale.users_at_launch", []).append((val, 0.75, span))
            elif _has(r"\b(12|twelve) months?|a year|one year|year one|next year|within (the )?year|by (the )?end of|"
                      r"grow|reach|expect|scale to|target|projected|eventually|after launch", ctx):
                found.setdefault("scale.users_12_months", []).append((val, 0.8, span))
            else:
                found.setdefault("scale.users_12_months", []).append((val, 0.5, span))
    for path, vals in found.items():
        for v, conf, span in vals:
            c.add(path, v, conf, span)
    # peak stated as "peak of 800" / "800 at peak"
    for m in re.finditer(r"(?<![\w.])(" + _NUM + r")" + _MULTRE + r"\s+(?:users\s+|customers\s+)?(?:at|on|by|during)\s+(?:launch|day one|go-live)\b|"
                         r"\b(?:launch|start|begin)(?:ing)?\s+with\s+(?:around\s+|about\s+)?(" + _NUM + r")" + _MULTRE + r"(?!\s*(?:weeks?|months?|days?))", c.text, re.I):
        numtxt = re.search(_NUM + _MULTRE, m.group(0), re.I).group(0)
        v = _split_num(numtxt)
        if v:
            c.add("scale.users_at_launch", int(v), 0.75, m.span())
    for m in re.finditer(r"\bpeak(?: load| traffic)?(?: of| at| around| about)?\s+(" + _NUM + r")" + _MULTRE +
                         r"(?!\s*(?:ms|%|gb|rps))", c.text, re.I):
        v = _split_num(re.match(r"\bpeak(?: load| traffic)?(?: of| at| around| about)?\s+(.*)", m.group(0), re.I).group(1))
        if v:
            c.add("scale.peak_concurrent_users", int(v), 0.7, m.span())
    for m in re.finditer(r"(" + _NUM + r")" + _MULTRE + r"\s+(?:requests?|req)\s*(?:/|per\s)\s*(?:s|sec|second)\b|"
                         r"(" + _NUM + r")" + _MULTRE + r"\s*(?:rps|req/s|qps)\b", c.text, re.I):
        numtxt = re.match(r"(" + _NUM + r")" + _MULTRE, m.group(0), re.I).group(0)
        v = _split_num(numtxt)
        if v:
            c.add("extra.expected_rps", v, 0.8, m.span())


def _team(c):
    t = c.text
    for m in re.finditer(r"\b(?:team|group|squad)\s+of\s+(" + _NUM + r"|" + "|".join(_WORDNUM) + r")\b", t, re.I):
        w = m.group(1).lower()
        v = _WORDNUM.get(w) or int(float(w))
        c.add("team.size", int(v), 0.85, m.span())
    for m in re.finditer(r"\b(" + _NUM + r"|" + "|".join(k for k in _WORDNUM if k not in ("a", "single")) +
                         r")[\s-]+(?:person|member|engineer|developer|dev|programmer|coder|student)s?\b", t, re.I):
        w = m.group(1).lower()
        v = _WORDNUM.get(w) or int(float(w))
        if 1 <= v <= 500:
            c.add("team.size", int(v), 0.8, m.span())
    for m in re.finditer(r"\b(solo (?:developer|founder|dev|builder)|one-man|one-woman|just me|only me|by myself|"
                         r"i am (?:a )?(?:single|the only) developer|single developer|sole developer)\b", t, re.I):
        c.add("team.size", 1, 0.8, m.span())
    rules = [
        ("beginner", r"\b(beginners?|novices?|first[- ]time|first project|college students?|students?|fresher|"
                     r"learning to code|new to (?:programming|coding|web))\b", 0.7),
        ("senior", r"\b(senior|seasoned|veteran|highly experienced|staff engineers?|principal)\b", 0.75),
        ("intermediate", r"\bintermediate\b|\b(?:2|3|two|three)\+? years(?:'|\s)? experience", 0.7),
        ("mixed", r"\bmixed (?:experience|team|skills?|levels?)|mix of (?:senior|junior|experience)|"
                  r"(?:junior|freshers?) and (?:senior|experienced)|(?:senior|experienced) and (?:junior|freshers?)", 0.8),
    ]
    for val, rx, conf in rules:
        for m in re.finditer(rx, t, re.I):
            c.add("team.experience", val, conf, m.span())
    langs = [
        ("python", r"python|django|fastapi|flask", ),
        ("typescript", r"typescript|\bts\b|nestjs|angular"),
        ("javascript", r"javascript|\bjs\b|node(?:\.?js)?|react|vue|express(?:\.js)?|next\.?js|svelte"),
        ("java", r"\bjava\b|spring boot|spring\b|kotlin multiplatform"),
        ("csharp", r"c#|csharp|\.net\b|dotnet|asp\.net|blazor"),
        ("php", r"\bphp\b|laravel|wordpress|symfony"),
        ("go", r"golang|\bgo (?:developers?|engineers?|backend|services?)\b"),
        ("kotlin", r"\bkotlin\b"),
        ("swift", r"\bswift\b(?!ly)|swiftui"),
        ("ruby", r"\bruby\b|\brails\b"),
        ("rust", r"\brust\b"),
        ("dart", r"\bdart\b|\bflutter\b"),
        ("sql", r"\bsql\b|postgres|mysql"),
        ("cpp", r"c\+\+|\bcpp\b"),
    ]
    skill_cue = re.compile(r"\b(know|knows|knowing|skilled|skills?|experience[ds]?|familiar|comfortable|proficient|"
                           r"expertise|background|team (?:uses|works|writes|codes)|developers?|engineers?|can write|"
                           r"write|learned|learnt|have been using)\b", re.I)
    for lang, rx in langs:
        explicit = lang if lang not in ("cpp",) else "c++"
        for sent, off in _sentences(t):
            for m in re.finditer(r"(?<![\w-])(?:" + rx + r")(?![\w-])", sent, re.I):
                word = m.group(0).lower()
                is_name = word.replace("#", "sharp").replace("+", "p") in (lang, "csharp", "cpp", "golang", "js", "ts")
                if lang == "go" and not skill_cue.search(sent):
                    continue
                cue = skill_cue.search(sent)
                if is_name and cue:
                    conf = 0.85
                elif is_name:
                    conf = 0.6
                else:
                    conf = 0.5 if cue else 0.4
                if lang == "typescript" and word == "angular":
                    conf = 0.35
                c.add("team.languages", lang, conf, (off + m.start(), off + m.end()))
    for m in re.finditer(r"\b(?:no|without|zero|lack(?:s|ing)?(?: of)?)\s+(?:a\s+|any\s+)?(?:dedicated\s+)?"
                         r"(?:dev[- ]?ops|sre|ops|infra(?:structure)?|sysadmin|platform)\b", t, re.I):
        c.add("team.has_devops", False, 0.85, m.span())
    for m in re.finditer(r"\b(?:dev[- ]?ops (?:engineer|team|person|lead)|dedicated (?:dev[- ]?ops|sre|ops)|"
                         r"(?:has|have|with|our) (?:a |an )?(?:dev[- ]?ops|sre|sysadmin|infra team)|sre team)\b", t, re.I):
        if not c.negated(m.start()):
            c.add("team.has_devops", True, 0.8, m.span())


_CREDIT_TAIL = re.compile(r"\s*(?:in|of|worth of)?\s*(?:(?:free|startup|promotional|cloud|aws|gcp|google cloud|azure|microsoft)\s+){0,3}credits?\b", re.I)
_CREDIT_RX = re.compile(r"(?P<cur>\$|usd\s?|us\$\s?|₹|rs\.?\s?|inr\s?)\s?(?P<n>" + _NUM + r")(?P<m>\s?(?:k|thousand|lakhs?|lacs?|crores?|cr|m|mn|million)\b)?"
                        r"(?P<tail>\s*(?:in|of|worth of)?\s*(?:(?:free|startup|promotional|cloud|aws|gcp|google cloud|azure|microsoft)\s+){0,3}credits?\b)", re.I)


def _credits(c):
    for m in _CREDIT_RX.finditer(c.text):
        val = _num(m.group("n"), m.group("m"))
        note = None
        if not m.group("cur").strip().startswith(("$", "usd", "us$", "USD", "US$")):
            val = val / INR_PER_USD
            note = "converted at INR %d per USD" % INR_PER_USD
        c.add("extra.cloud_credits_usd", round(val, 2) if val % 1 else int(val), 0.75, m.span(), note)


def _money(c):
    t = c.text
    cur = (r"(?P<usd>\$|usd\s?|us\$\s?|dollars?\s)|(?P<inr>₹|rs\.?\s?|inr\s?|rupees?\s?)")
    rx = re.compile(r"(?:" + cur + r")\s?(?P<n>" + _NUM + r")(?P<m>\s?(?:k|thousand|lakhs?|lacs?|crores?|cr|m|mn|million)\b)?"
                    r"|(?P<n2>" + _NUM + r")(?P<m2>\s?(?:k|thousand|lakhs?|lacs?|crores?|cr)\b)?\s*"
                    r"(?:(?P<usd2>usd|dollars?|us dollars?)|(?P<inr2>inr|rupees?|rs\b\.?))", re.I)
    for sent, off in _sentences(t):
        if not _has(r"budget|per month|/month|/mo\b|monthly|a month|infra|hosting|spend|afford|cost|pay|"
                    r"free tier|a year|annual|per year|yearly", sent):
            continue
        if _has(r"\b(revenue|salary|salaries|funding|raised|price of|charge|subscription fee|per user)\b", sent) and \
                not _has(r"budget|infra|hosting", sent):
            continue
        for m in rx.finditer(sent):
            n = m.group("n") or m.group("n2")
            mult = m.group("m") or m.group("m2")
            val = _num(n, mult)
            inr = bool(m.group("inr") or m.group("inr2"))
            note = None
            if inr:
                val = val / INR_PER_USD
                note = "converted at INR %d per USD" % INR_PER_USD
            tail = sent[m.end():m.end() + 25]
            if _has(r"per year|/year|/yr|a year|annual|yearly|p\.a\.", tail + " " + sent[:m.start()][-20:]) and \
                    not _has(r"per month|/month|monthly|a month", tail):
                val = val / 12
                note = ((note + "; ") if note else "") + "yearly amount divided by 12"
            if _has(r"\bper\s+(?:user|seat|transaction|sms|order)", tail) or _CREDIT_TAIL.match(tail):
                continue
            conf = 0.85 if _has(r"budget|per month|/month|monthly|a month|infra", sent) else 0.55
            c.add("budget.monthly_infra_usd", round(val, 2) if val % 1 else int(val), conf,
                  (off + m.start(), off + m.end()), note)
    for m in re.finditer(r"\b(?:zero|no|nil)\s+budget\b|\bbudget\s+(?:is\s+)?(?:zero|nil|0)\b|\bfree[- ]tier\s+only\b|"
                         r"\bonly\s+free\s+(?:tier|plans?|services?|hosting)\b|\bshoestring\b|\bcan't afford (?:anything|to pay)\b|"
                         r"\bwe have no money\b", t, re.I):
        c.add("budget.monthly_infra_usd", 0, 0.85, m.span())


def _timeline(c):
    unit = r"(weeks?|wks?|months?|days?)"
    rx = re.compile(r"\b(" + _NUM + r"|" + "|".join(k for k in _WORDNUM if k not in ("a", "single", "solo")) +
                    r")(?:\s?-\s?(?:" + _NUM + r"))?\s*" + unit + r"\b", re.I)
    cue = (r"mvp|launch|ship|deliver|release|go[- ]live|going live|deadline|timeline|prototype|demo|first version|"
           r"ready|build|within|in \w+ (?:weeks?|months?)|time\s?frame|before|v1|pilot|hackathon|schedule")
    for sent, off in _sentences(c.text):
        cued = bool(_has(cue, sent))
        for m in rx.finditer(sent):
            w = m.group(1).lower()
            v = _WORDNUM.get(w) or float(w.replace(",", ""))
            u = m.group(2).lower()
            after = sent[m.end():m.end() + 25]
            if _has(r"^\s*(?:of\s+(?:experience|data|logs)|old|retention|ago|per|each|every)\b", after):
                continue
            if _has(r"(?:every|each|per|last|past|for)\s*$", sent[max(0, m.start() - 12):m.start()]):
                continue
            if _has(r"(?:users|customers|members|subscribers|growth|grow|reach|expect\w*|scale|accounts)\b[^,;]{0,25}$",
                    sent[max(0, m.start() - 40):m.start()]):
                continue
            if u.startswith("m"):
                weeks = v * 4.345
            elif u.startswith("d"):
                weeks = v / 7.0
            else:
                weeks = v
            weeks = max(1, int(round(weeks)))
            strong = _has(r"mvp|first (?:version|release)|go[- ]live|launch|deliver|ship|v1|prototype|deadline", sent)
            if not cued and (u.startswith("d") or _has(r"^\s*(?:to go|left)", after)):
                continue
            c.add("timeline.mvp_weeks", weeks, 0.85 if strong else (0.5 if cued else (0.45 if u.startswith("w") else 0.4)), (off + m.start(), off + m.end()),
                  "%s %s" % (m.group(1), u) if u.startswith("m") else None)


_PLATFORM_RULES = [
    ("web", r"\bweb\s?(?:site|app|application|portal|dashboard|platform|version|frontend|ui)\b|\bwebsite\b|\bbrowser\b|"
            r"\bweb\b(?![- ]?scrap)|\bresponsive\b|\bpwa\b|\bprogressive web\b", 0.8),
    ("android", r"\bandroid\b|\bplay store\b|\bgoogle play\b", 0.9),
    ("ios", r"\bios\b|\biphone\b|\bipad\b|\bapp store\b|\bapple devices\b", 0.9),
    ("desktop", r"\bdesktop (?:app|application|client|software)\b|\belectron\b|\bwindows (?:app|application|client)\b|"
                r"\bmac(?:os)? (?:app|application)\b|\bdesktop\b", 0.75),
]


def _platforms(c):
    t = c.text
    for val, rx, conf in _PLATFORM_RULES:
        for m in re.finditer(rx, t, re.I):
            if c.negated(m.start()) and val != "web":
                continue
            c.add("project.platforms", val, conf, m.span())
    for m in re.finditer(r"\bmobile\s+(?:app|application|apps|first|client)\b|\bmobile\b(?!\s+(?:number|phone|money|wallet|payments?|otp))", t, re.I):
        if c.negated(m.start()):
            continue
        c.add("project.platforms", "android", 0.5, m.span(), "mobile app: platform not stated")
        c.add("project.platforms", "ios", 0.45, m.span(), "mobile app: platform not stated")
    for m in re.finditer(r"\bapi[- ]only\b|\bheadless\b|\bno (?:ui|frontend|front-end|user interface)\b|"
                         r"\bbackend[- ]only\b|\bjust (?:an? )?api\b|\bpublic api\b|\bdeveloper api\b|\brest api for\b", t, re.I):
        if c.negated(m.start()) and "no ui" not in m.group(0).lower():
            continue
        c.add("project.platforms", "api_only", 0.7 if "public" not in m.group(0).lower() else 0.45, m.span())
    for m in re.finditer(r"\b(?:cross[- ]platform|single codebase|react native|flutter)\b", t, re.I):
        c.add("extra.mobile_preference", "cross_platform", 0.7, m.span())
    for m in re.finditer(r"\bnative (?:android|ios|app|apps|mobile)\b|\bswiftui\b|\bjetpack compose\b", t, re.I):
        c.add("extra.mobile_preference", "native", 0.6, m.span())
    for m in re.finditer(r"\bpwa\b|\bprogressive web app\b|\binstallable web app\b", t, re.I):
        c.add("extra.mobile_preference", "pwa", 0.7, m.span())


_TYPE_RULES = {
    "content_site": r"blog|brochure|portfolio|landing page|marketing site|information site|info site|news site|documentation site|"
                    r"static site|college (?:fest|website)|fest\b|event (?:site|website|info)|content site|magazine|publication|"
                    r"announcements|schedule and (?:speakers|venue)|company website",
    "web_app": r"web app|dashboard|portal|booking system|management system|scheduling",
    "saas_b2b": r"saas|b2b|subscription software|tenants?|white[- ]label|per[- ]seat|crm\b|helpdesk|workspace",
    "ecommerce": r"e-?commerce|online (?:store|shop)|shopping|cart\b|checkout|product catalogue|catalog|storefront|merchandise|d2c",
    "marketplace": r"marketplace|providers? and customers|sellers? and buyers|vendors? and|two[- ]sided|gig\b|freelanc|"
                   r"book (?:electricians|plumbers|drivers|tutors|providers|services)|listing platform|"
                   r"customers book|providers accept|hire (?:professionals|providers)",
    "social": r"social (?:network|media|app)|news ?feed|\bfeed\b|followers?|\bposts?\b|community|friends|likes?\b|"
              r"comments?\b|profiles?\b|dating",
    "realtime_collab": r"collaborat|co-?edit|whiteboard|shared documents?|real-?time editing|live editing|multiplayer editing|"
                       r"shared canvas|presence|cursors",
    "fintech": r"fintech|lending|loans?\b|\bbank|neobank|wallet|\bupi\b|kyc|trading|brokerage|insurance|credit|"
               r"payment (?:gateway|platform|processor)|remittance|ledger|mutual fund|nbfc|rbi",
    "healthtech": r"hospital|clinic|patient|telemedicine|telehealth|\behr\b|\bemr\b|health records?|doctors?|medical|"
                  r"pharmacy|diagnostic|hipaa|appointment booking",
    "data_analytics": r"analytics|\betl\b|data warehouse|\bbi\b|reporting|data pipeline|big data|dashboards? (?:for|of) data|"
                      r"clickstream|olap|data lake",
    "iot": r"\biot\b|sensors?|telemetry|firmware|mqtt|smart (?:meter|home|device)|embedded|edge devices?|devices? fleet",
    "ai_ml_product": r"\bllm\b|\brag\b|chatbot|machine learning|\bml\b|ai[- ]powered|generative|copilot|\bgpt\b|"
                     r"model training|recommendation engine|computer vision|\bai (?:assistant|product|app|tool)\b",
    "internal_tool": r"internal (?:tool|app|portal|system|use)|employees?\b|back-?office|admin tool|company staff|\bhr\b|"
                     r"\berp\b|intranet|our (?:staff|team members|sales team|warehouse)|inventory management",
    "gaming": r"\bgames?\b|gaming|multiplayer|leaderboards?|esports|\bunity\b|matchmaking|\bplayers?\b",
    "api_platform": r"api platform|public api|developer platform|\bsdk\b|webhooks?|api[- ]first|api as a product|"
                    r"api marketplace|\bapis? (?:for|to) (?:third|external|partners|developers)",
}
_TYPE_ORDER = list(_TYPE_RULES)


def _project_type(c):
    t = c.text
    scores = {}
    first = {}
    for typ, rx in _TYPE_RULES.items():
        hits = set()
        for m in re.finditer(r"(?<![\w-])(?:" + rx + r")", t, re.I):
            hits.add(m.group(0).lower())
            first.setdefault(typ, m.span())
        scores[typ] = len(hits)
    # generic web_app words are weak signals
    scores["web_app"] = scores["web_app"] * 0.5
    scores["social"] = scores["social"] * 0.7
    scores["gaming"] = scores["gaming"] * 0.9
    scores["internal_tool"] = scores["internal_tool"] * 0.8
    ranked = sorted((typ for typ in scores if scores[typ] > 0), key=lambda k: (-scores[k], _TYPE_ORDER.index(k)))
    if not ranked:
        return
    best = ranked[0]
    margin = scores[best] - (scores[ranked[1]] if len(ranked) > 1 else 0)
    conf = min(0.85, 0.45 + 0.12 * scores[best] + 0.08 * margin)
    c.add("project.type", best, conf, first[best])
    for other in ranked[1:3]:
        if scores[other] >= scores[best] * 0.6:
            c.add("project.type", other, max(0.3, conf - 0.2), first[other])


_BOOL_RULES = [
    ("requirements.realtime", r"\bchat(?:ting)?\b|\blive (?:chat|updates?|tracking|location|scores?|feed|collaboration|"
     r"notifications?|bidding|streaming|dashboard|map)\b|real[- ]?time|\bpresence\b|websockets?|\bpush updates?\b|"
     r"collaborat\w+|instant (?:messag\w+|updates?|notifications?)|messaging|co-?edit\w*|multiplayer|"
     r"track(?:ing)? (?:the )?(?:driver|rider|delivery|order)s? (?:live|in real)|stream(?:ing)? data", 0.8),
    ("requirements.offline_support", r"\boffline\b|without (?:an? )?(?:internet|connectivity|network)|"
     r"(?:poor|low|patchy|intermittent|limited|spotty|no) (?:internet|connectivity|network)|work(?:s|ing)? in remote|"
     r"sync(?:s|ed|ing)? (?:when|once|later)|no signal|local[- ]first", 0.8),
    ("requirements.full_text_search", r"\bsearch(?:able|ing)?\b(?!\s*engines?\b)|\bfull[- ]text\b|\bfind (?:products|items|documents|"
     r"providers|listings|people)\b|\bautocomplete\b|\bfilters? and (?:sort|search)", 0.75),
    ("requirements.file_media_storage", r"\buploads?\b|\bupload(?:ed|ing)?\b|\bphotos?\b|\bimages?\b|\bvideos?\b|\battachments?\b|\bmedia\b|"
     r"\bfile (?:storage|sharing)\b|\bscans?\b|\breceipts?\b|\bpdfs?\b|\bdocuments? (?:storage|upload)|\bavatars?\b|\bgallery\b|"
     r"\bscanned\b|\bx-?rays?\b", 0.65),
    ("requirements.payments", r"\bpayments?\b|\bpay(?:ing)? (?:online|for|by|with|using)\b|\bget(?:s|ting)? paid\b|\bcheckout\b|"
     r"\bbilling\b|\bsubscriptions?\b|\bupi\b|\bstripe\b|\brazorpay\b|\bcashfree\b|\bpaypal\b|\bpayu\b|\bcard payments?\b|"
     r"\binvoic\w+|\bwallets?\b|\bpay-?outs?\b|\bcharge (?:customers|users)\b|\bcredit cards?\b|\bnet ?banking\b|\bcollect(?:s|ing)? money", 0.8),
    ("requirements.multi_tenant", r"multi[- ]?tenan\w+|\btenants?\b|multiple (?:organi[sz]ations|companies|clinics|hospitals|schools|"
     r"clients|customers' organi[sz]ations|businesses|stores|workspaces)|white[- ]label|each (?:customer|company|clinic|school|"
     r"client|organi[sz]ation|business) (?:has|gets) (?:its|their) own|isolated (?:data|workspaces)", 0.8),
    ("requirements.seo_critical", r"\bseo\b|search[- ]engines?|rank(?:s|ing)? (?:on|in) (?:google|search)|google (?:rank|search|"
     r"discover)\w*|organic (?:traffic|search)|discoverab\w+ (?:on|via|through) (?:google|search)|\bsitemaps?\b|"
     r"public pages (?:must |should |need to )?(?:rank|be indexed)|indexed by google", 0.85),
]


def _booleans(c):
    t = c.text
    for path, rx, conf in _BOOL_RULES:
        for m in re.finditer(rx, t, re.I):
            neg = c.negated(m.start())
            word = m.group(0).lower()
            if path == "requirements.realtime" and word in ("messaging",) and not _has(r"chat|live|instant", t):
                conf_m = 0.5
            else:
                conf_m = conf
            if path == "requirements.file_media_storage" and word in ("media", "image", "images", "photo", "photos") \
                    and _has(r"social media|media (?:coverage|outlet)|photo ?(?:id|shoot)", t[max(0, m.start() - 10):m.end() + 12]):
                continue
            if path == "requirements.full_text_search" and _has(r"search (?:the )?(?:web|internet)", t[m.start():m.end() + 15]):
                continue
            if path == "requirements.seo_critical" and "search engine" in word and _has(r"search engine (?:optimi|rank|index|visib|friendly)", t[m.start():m.end() + 20]) is None \
                    and not _has(r"rank|index|visib|optimi|friendly|discover", t[max(0, m.start() - 30):m.end() + 40]):
                continue
            if neg:
                c.add(path, False, 0.7, m.span())
            else:
                c.add(path, True, conf_m, m.span())


def _auth(c):
    t = c.text
    rules = [
        ("phone_otp", r"\botps?\b|one[- ]time (?:password|passcode|code)|(?:phone|mobile)(?: number)? (?:login|sign-?in|verification|"
                      r"auth\w*)|sms (?:login|verification|code)|log ?in with (?:phone|mobile)|verify (?:their |the )?(?:phone|mobile)", 0.9),
        ("enterprise_sso", r"\bsso\b|single sign[- ]on|\bsaml\b|\boidc\b|active directory|\bentra\b|azure ad|\bokta\b|\bldap\b|"
                           r"corporate (?:login|accounts?|credentials)|company (?:login|accounts?|credentials)|(?:microsoft|office) ?365 (?:login|accounts?|sign)", 0.85),
        ("social", r"(?:google|facebook|apple|github|linkedin|social) (?:login|sign-?in|auth\w*|accounts?)|sign in with (?:google|apple|"
                   r"facebook|github)|log ?in with (?:google|apple|facebook|github)|oauth|social (?:login|auth)", 0.85),
        ("email_password", r"e-?mail(?: and| &|/)? ?password|username(?: and| &)? password|password login|login with e-?mail|"
                           r"e-?mail (?:login|sign-?up|registration)", 0.85),
    ]
    for val, rx, conf in rules:
        for m in re.finditer(rx, t, re.I):
            if c.negated(m.start()):
                continue
            c.add("requirements.auth", val, conf, m.span())
    for m in re.finditer(r"\b(?:log ?in|logins?|sign[- ]?up|sign[- ]?in|registers?|registration|user accounts?|accounts?|"
                         r"authentication|profiles?|members?-only)\b", t, re.I):
        if c.negated(m.start()):
            continue
        c.add("requirements.auth", "email_password", 0.4, m.span(), "accounts mentioned, method not stated")
        break
    for m in re.finditer(r"\bno (?:log ?in|logins|accounts?|sign[- ]?ups?|registration|authentication|user accounts)\b|"
                         r"\bwithout (?:log ?in|accounts|sign[- ]?up|registration)\b|\banonymous (?:access|users|visitors)\b|"
                         r"\bno user accounts\b|\bpublicly accessible\b", t, re.I):
        c.add("requirements.auth", "none", 0.75, m.span())


def _ml(c):
    t = c.text
    rules = [
        ("llm_rag", r"\brag\b|retrieval[- ]augmented|\bllms?\b|large language model|chat ?bot|\bgpt\b|gemini|embeddings?|vector (?:db|database|search|store)|"
                    r"generative ai|ask questions (?:over|about|from)|question[- ]answering|semantic search|\bcopilot\b", 0.85),
        ("training", r"train(?:s|ing)? (?:a |our |their |own |custom |its )?(?:own |custom )?(?:ml |machine learning |deep learning |neural )?models?|fine[- ]?tun\w+|"
                     r"model training|deep learning|neural net\w*|gpu training|own (?:ml )?models?", 0.8),
        ("inference_api", r"machine learning|\bml\b|recommendation(?:s| engine| system)|image recognition|classification|classif\w+|prediction|"
                          r"forecast\w*|computer vision|\bocr\b|sentiment|fraud detection|anomaly detection|\bai[- ]powered\b|\bai\b|"
                          r"speech|translation|personali[sz]ation", 0.6),
    ]
    for val, rx, conf in rules:
        for m in re.finditer(rx, t, re.I):
            if c.negated(m.start()):
                continue
            c.add("requirements.ml_features", val, conf, m.span())


def _quality(c):
    t = c.text
    for m in re.finditer(r"(?:uptime|availability|sla)[^.\n]{0,40}?(99(?:\.\d+)?)\s*%|(99(?:\.\d+)?)\s*%\s*(?:uptime|availability|sla)", t, re.I):
        v = float(m.group(1) or m.group(2))
        norm = "99.99" if v >= 99.99 else "99.95" if v >= 99.95 else "99.9" if v >= 99.9 else "99"
        c.add("requirements.availability", norm, 0.9, m.span())
    for m in re.finditer(r"\b(five|four|three) nines\b", t, re.I):
        c.add("requirements.availability", {"five": "99.99", "four": "99.99", "three": "99.9"}[m.group(1).lower()], 0.8, m.span())
    for m in re.finditer(r"\b(?:mission[- ]critical|zero downtime|no downtime|always[- ]on|24/7 (?:availability|uptime|operation))\b", t, re.I):
        c.add("requirements.availability", "99.99" if "zero" in m.group(0).lower() or "no down" in m.group(0).lower() else "99.95",
              0.55, m.span())
    lat = re.compile(r"(?:p9[59]|latency|response time|respond|responses?|load time|loads?)[^.\n]{0,40}?(?:under|below|within|less than|<|<=|"
                     r"under|max(?:imum)?|of|at|around|about|:)?\s*(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?)\b|"
                     r"(?:under|below|within|less than|<)\s*(\d+(?:\.\d+)?)\s*(ms|milliseconds?)\b", re.I)
    for m in lat.finditer(t):
        n = m.group(1) or m.group(3)
        u = (m.group(2) or m.group(4)).lower()
        v = float(n)
        if u.startswith("s"):
            v *= 1000
        if 1 <= v <= 60000:
            c.add("requirements.p95_latency_ms", int(round(v)), 0.8, m.span())
    for m in re.finditer(r"\bp9[59]\b[^.\n]{0,30}?(\d+)\s*ms", t, re.I):
        c.add("requirements.p95_latency_ms", int(m.group(1)), 0.9, m.span())
    for m in re.finditer(r"(?<![\d.])(\d+)\s*ms\b", t, re.I):
        v = int(m.group(1))
        if 5 <= v <= 5000 and _has(r"latency|lag|response|fast|realtime|real-time|ping|delay", t[max(0, m.start() - 60):m.end() + 40]):
            c.add("requirements.p95_latency_ms", v, 0.6, m.span())
    for m in re.finditer(r"\b(read[- ]heavy|mostly (?:reads|static|read-only|browsing)|content[- ]heavy|mostly read)\b", t, re.I):
        c.add("scale.workload", "read_heavy", 0.75, m.span())
    for m in re.finditer(r"\b(write[- ]heavy|mostly writes|ingest\w*|high[- ]volume (?:writes|events|telemetry)|time[- ]series|"
                         r"telemetry|logging|sensor (?:data|readings))\b", t, re.I):
        c.add("scale.workload", "write_heavy", 0.7, m.span())
    for m in re.finditer(r"\b(spiky|spikes?|bursty|burst traffic|flash sales?|viral|traffic surges?|ticket drops?|launch[- ]day|"
                         r"peak (?:days?|weekends?|hours)|fest (?:week|days?)|sale days?|match days?)\b", t, re.I):
        c.add("scale.traffic_pattern", "spiky", 0.75, m.span())
    for m in re.finditer(r"\b(seasonal|festival season|holiday (?:season|rush)|exam season|tax season|harvest season|"
                         r"admission season|annual peak|during (?:diwali|christmas|black friday))\b", t, re.I):
        c.add("scale.traffic_pattern", "seasonal", 0.75, m.span())
    for m in re.finditer(r"\b(" + _NUM + r")\s?(gb|tb|pb|gigabytes?|terabytes?|petabytes?)\b", t, re.I):
        ctx = t[max(0, m.start() - 70):m.end() + 70]
        if not _has(r"data|storage|stored|store|files|videos|photos|images|logs|records|database|backup|documents|archive|disk", ctx):
            continue
        if _has(r"\bram\b|memory|bandwidth|egress|per month of traffic", t[m.start():m.end() + 20]) and not _has(r"stor", ctx):
            continue
        v = float(m.group(1).replace(",", ""))
        u = m.group(2).lower()[0]
        gb = v * (1000 if u == "t" else 1_000_000 if u == "p" else 1)
        c.add("scale.data_volume_gb_12_months", int(gb) if gb == int(gb) else gb, 0.7, m.span())


_COMPLIANCE = [
    ("hipaa", r"\bhipaa\b|protected health information|\bphi\b", 0.9),
    ("pci_dss", r"\bpci(?:[- ]?dss)?\b|card (?:data|holder) (?:security|data)", 0.9),
    ("gdpr", r"\bgdpr\b|general data protection", 0.9),
    ("dpdp", r"\bdpdp(?:a)?\b|digital personal data protection", 0.9),
    ("rbi_localisation", r"\brbi\b|reserve bank of india|payment data (?:localis|localiz)\w+|data localis\w+|data localiz\w+", 0.85),
    ("soc2", r"\bsoc[- ]?2\b", 0.9),
    ("iso27001", r"\biso[- ]?27001\b|\biso 27k\b", 0.9),
]

_REGIONS = [
    ("india", r"\bindia\b|\bindian\b|\bin-country\b|mumbai|hyderabad|delhi|bengaluru|bangalore|chennai|pune|kolkata|ahmedabad|jaipur|"
              r"kochi|lucknow|indore|nagpur|surat|gurugram|gurgaon|noida|karnataka|maharashtra|kerala|tamil nadu|gujarat|"
              r"\binr\b|₹|\brupees?\b|\blakhs?\b|\bcrores?\b|\bupi\b", ),
    ("eu", r"\beuropean union\b|\beu\b|\beurope\b|\beuropean\b|frankfurt|germany|france|paris|netherlands|amsterdam|ireland|"
           r"spain|italy|sweden|poland|berlin|\bgdpr\b"),
    ("us", r"\bunited states\b|\busa\b|\bu\.s\.\b|\bus-only\b|\bus customers\b|\bus-based\b|\bamerican\b|virginia|oregon|california|texas|new york"),
]
_RESIDENCY_CUE = (r"residen\w+|stay(?:s)? (?:in|within)|must (?:be )?(?:stored|hosted|kept|processed|reside)|stored (?:in|within)|"
                  r"hosted (?:in|within)|localis\w+|localiz\w+|in-country|sovereign\w*|within (?:the )?(?:country|india|eu|europe|us)|"
                  r"data (?:centre|center)s? in|servers? in|only in|not leave|leave the country|region")


def _compliance_region(c):
    t = c.text
    for val, rx, conf in _COMPLIANCE:
        for m in re.finditer(rx, t, re.I):
            if c.negated(m.start()) and val in ("hipaa", "pci_dss"):
                continue
            c.add("compliance", val, conf, m.span())
    for sent, off in _sentences(t):
        cue = _has(_RESIDENCY_CUE, sent)
        for region, rx in _REGIONS:
            for m in re.finditer(rx, sent, re.I):
                word = m.group(0).lower()
                if region == "eu" and word == "gdpr" and not cue:
                    continue
                strong = word not in ("inr", "₹", "rupee", "rupees", "lakh", "lakhs", "crore", "crores", "upi")
                if cue and strong:
                    c.add("infrastructure.data_residency", region, 0.85, (off + m.start(), off + m.end()))
                elif region == "india":
                    c.add("infrastructure.data_residency", region, 0.4 if strong else 0.35, (off + m.start(), off + m.end()),
                          "place or currency mention, not an explicit residency rule")
                break
    if _has(r"\brbi\b|reserve bank", t):
        m = _has(r"\brbi\b|reserve bank", t)
        c.add("infrastructure.data_residency", "india", 0.7, m.span(), "RBI rules require payment data in India")
    if _has(r"\bdpdp(a)?\b|digital personal data protection", t):
        m = _has(r"\bdpdp(a)?\b|digital personal data protection", t)
        c.add("infrastructure.data_residency", "india", 0.5, m.span(), "DPDP applies to Indian users' data")


_CLOUDS = [("aws", r"\baws\b|amazon web services|\bec2\b|\bs3\b|lambda"), ("gcp", r"\bgcp\b|google cloud|\bgke\b|cloud run|firebase"),
           ("azure", r"\bazure\b|microsoft cloud")]
_SYSTEMS = [("SQL Server", r"sql server|mssql"), ("Oracle", r"\boracle\b"), ("SAP", r"\bsap\b"), ("Salesforce", r"salesforce"),
            ("Active Directory", r"active directory|\bldap\b"), ("Microsoft 365", r"microsoft 365|office 365|\bm365\b|\bo365\b"),
            ("Entra ID", r"entra(?: id)?|azure ad"), ("Tally", r"\btally\b"), ("Zoho", r"\bzoho\b"), ("QuickBooks", r"quickbooks"),
            ("Shopify", r"shopify"), ("WordPress", r"wordpress"), ("MySQL", r"\bmysql\b"), ("PostgreSQL", r"postgres(?:ql)?"),
            ("MongoDB", r"mongodb"), ("Excel", r"\bexcel\b|spreadsheets?"), ("ERP", r"\berp\b"), ("Jira", r"\bjira\b"),
            ("Slack", r"\bslack\b"), ("SharePoint", r"sharepoint"), ("Power BI", r"power ?bi"), ("Kafka", r"\bkafka\b"),
            ("Redis", r"\bredis\b"), ("AngularJS", r"angularjs|angular 1"), ("Python 2", r"python ?2(?:\.\d)?\b"),
            ("PHP monolith", r"legacy php|php monolith"), ("Mainframe", r"mainframe|cobol")]


def _infra(c):
    t = c.text
    for val, rx in _CLOUDS:
        for m in re.finditer(rx, t, re.I):
            conf = 0.7 if _has(r"prefer|stay on|only|already|must|credits|use|run on|hosted on|deploy(?:ed)? (?:on|to)|committed|standardi[sz]e", t[max(0, m.start() - 60):m.end() + 40]) else 0.45
            c.add("infrastructure.cloud_preference", val, conf, m.span())
            if _has(r"credits?", t[max(0, m.start() - 20):m.end() + 25]):
                c.add("extra.existing_licenses", val + "_credits", 0.7, m.span())
            break
    for m in re.finditer(r"\bno cloud\b|\bwithout (?:the )?cloud\b|\bnot (?:use|allowed to use) (?:the )?cloud\b|\bavoid(?:ing)? (?:the )?cloud\b", t, re.I):
        c.add("infrastructure.cloud_preference", "none", 0.75, m.span())
    for m in re.finditer(r"\bair[- ]?gapped?\b|\bno (?:internet|external network) (?:access|connectivity)\b|\bisolated network\b|"
                         r"\bnot connected to the internet\b|\bclassified network\b", t, re.I):
        c.add("infrastructure.deployment_model", "air_gapped", 0.9, m.span())
    for m in re.finditer(r"\bon[- ]?prem(?:ises?|ise)?\b|\bown (?:servers?|data ?cent(?:er|re)s?|hardware)\b|\bin-house servers?\b|"
                         r"\bour (?:own )?data ?cent(?:er|re)\b|\bbare[- ]metal\b", t, re.I):
        c.add("infrastructure.deployment_model", "on_premise", 0.85, m.span())
    for m in re.finditer(r"\bself[- ]?host\w*\b|\bvps\b|\bdroplets?\b|\bsingle (?:vm|server)\b|\bour own vm\b|\bhetzner\b|\blinode\b", t, re.I):
        c.add("infrastructure.deployment_model", "self_hosted_cloud_vm", 0.6, m.span())
    for m in re.finditer(r"\b(?:fully )?managed (?:cloud|services?|hosting)\b|\bserverless\b|\bpaas\b", t, re.I):
        c.add("infrastructure.deployment_model", "managed_cloud", 0.5, m.span())
    cues = r"existing|already|legacy|integrat\w+|current(?:ly)?|reuse|we use|we have|migrat\w+|connect(?:ed)? to|sync with|plug into|currently on|run(?:s|ning)? on"
    for sent, off in _sentences(t):
        if not _has(cues, sent):
            continue
        for name, rx in _SYSTEMS:
            m = re.search(r"(?<![\w-])(?:" + rx + r")(?![\w-])", sent, re.I)
            if m:
                c.add("infrastructure.existing_systems", name, 0.75, (off + m.start(), off + m.end()))
    for m in re.finditer(r"(?:microsoft|office) ?365 licen[cs]es?|m365 licen[cs]es?|already (?:pay|paying) for (?:microsoft|office) ?365", t, re.I):
        c.add("extra.existing_licenses", "microsoft_365", 0.75, m.span())


def _priorities(c):
    t = c.text
    rules = [
        ("priorities.cost", 5, r"\b(?:bootstrapped|shoestring|tight budget|very limited budget|cost is (?:critical|key|the (?:top|main) )|"
                              r"as cheap as possible|lowest cost|minimi[sz]e cost|cost[- ]sensitive|low[- ]cost|cheap|free tier)\b"),
        ("priorities.time_to_market", 5, r"\b(?:asap|as soon as possible|time to market|as fast as possible|very tight deadline|rapid(?:ly)?|"
                                        r"quick(?:ly)? (?:launch|ship|release)|launch quickly|hackathon|in a hurry)\b"),
        ("priorities.security", 5, r"\b(?:security is (?:critical|a priority|paramount|key)|highly secure|bank[- ]grade|sensitive data|"
                                   r"strict security|security[- ]critical|confidential)\b"),
        ("priorities.scalability", 4, r"\b(?:scal(?:e|ing|able|ability)|millions of users|rapid growth|viral|hyper[- ]?growth)\b"),
        ("priorities.performance", 4, r"\b(?:low[- ]latency|high performance|very fast|blazing|snappy|real[- ]time|responsive under load)\b"),
        ("priorities.maintainability", 4, r"\b(?:maintainab\w+|long[- ]term|easy to maintain|clean code|sustainable|handover|hand over)\b"),
    ]
    for path, val, rx in rules:
        for m in re.finditer(rx, t, re.I):
            if c.negated(m.start()):
                continue
            c.add(path, val, 0.5, m.span(), "inferred from wording")
            break


def _extras(c):
    t = c.text
    for m in re.finditer(r"\b(?:happy|eager|willing|keen|excited|ready) to (?:learn|pick up)\b|\blearn(?:ing)? new (?:tech\w*|tools|frameworks?)\b", t, re.I):
        c.add("extra.learning_appetite", "high", 0.7, m.span())
    for m in re.finditer(r"\b(?:no time to learn|stick(?:ing)? (?:to|with) (?:what we know|familiar)|only (?:what|tools) we know|"
                         r"can'?t afford to learn|no learning curve)\b", t, re.I):
        c.add("extra.learning_appetite", "low", 0.75, m.span())
    for m in re.finditer(r"\b(?:avoid(?:ing)? (?:vendor )?lock[- ]?in|no (?:vendor )?lock[- ]?in|portab(?:le|ility)|open[- ]source only|"
                         r"cloud[- ]agnostic|vendor[- ]neutral)\b", t, re.I):
        if not c.negated(m.start()) or "no" in m.group(0).lower():
            c.add("extra.lock_in_tolerance", "low", 0.75, m.span())
    for m in re.finditer(r"\b(?:lock[- ]?in (?:is )?(?:fine|ok|okay|acceptable)|fine with (?:vendor )?lock[- ]?in)\b", t, re.I):
        c.add("extra.lock_in_tolerance", "high", 0.7, m.span())
    for sens, rx in [("health", r"patient|medical|clinical|health records?|diagnos\w+|\bphi\b|hipaa"),
                     ("financial", r"bank|loan|\bkyc\b|credit|card data|payments?|financial|trading|ledger|\bpan\b|aadhaar"),
                     ("personal", r"personal (?:data|information)|\bpii\b|customers' (?:data|details)|phone numbers?|addresses|user profiles?|"
                                  r"\bdpdp\b|\bgdpr\b|kids|children|students' (?:data|records)"),
                     ("internal", r"internal (?:data|documents|use)|confidential|company data|employee data|hr records"),
                     ("public", r"public (?:information|data|content)|no (?:personal|sensitive) data|open data|information (?:site|only)")]:
        m = _has(rx, t)
        if m:
            conf = 0.7 if sens in ("health", "financial") else 0.55
            c.add("extra.data_sensitivity", sens, conf, m.span())
    for m in re.finditer(r"\b(?:plan(?:s|ning)? to (?:hire|grow the team|expand the team)|will hire|hiring (?:soon|more|plans?)|grow(?:ing)? the team)\b", t, re.I):
        c.add("extra.hiring_plan", "aggressive" if _has(r"rapid|aggressive|quickly|many|double|triple", t[max(0, m.start() - 40):m.end() + 40]) else "slow", 0.55, m.span())
    for m in re.finditer(r"\b(?:no (?:plans? to hire|hiring)|won't hire|will not hire|team will stay (?:the same|small))\b", t, re.I):
        c.add("extra.hiring_plan", "none", 0.65, m.span())
    for val, rx in [("global", r"\b(?:global|worldwide|international|across the world|all over the world|multiple continents)\b"),
                    ("multi_region", r"\b(?:multiple countries|several countries|across (?:europe|asia|apac|the eu|regions)|multi[- ]region|south asia)\b"),
                    ("country", r"\b(?:across|all over|nationwide|pan)[- ]?(?:india|the country|the us|usa)\b|\bnationwide\b|\bthroughout india\b|\bindian (?:users|customers|market)\b"),
                    ("single_city", r"\b(?:one|a single|single|local|only in (?:one|a)) (?:city|town|campus|neighbou?rhood|college|school)\b|\b(?:two|three|2|3) cities\b|\bcampus only\b|\bon campus\b|\bcity[- ]wide\b")]:
        m = _has(rx, t)
        if m:
            c.add("extra.user_geography", val, 0.65, m.span())
            if val == "single_city" and _has(r"two|three|2|3", m.group(0)):
                c.add("extra.user_geography", "single_city", 0.55, m.span(), "a few cities counts as local")


def _name(c):
    for m in re.finditer(r"\b(?:called|named|name is|codename|project)\s+[\"'“‘]([^\"'”’\n]{2,40})[\"'”’]|\b(?:called|named)\s+([A-Z][\w]+(?:\s[A-Z][\w]+){0,3})", c.text):
        name = (m.group(1) or m.group(2)).strip()
        c.add("project.name", name, 0.6, m.span())
        break


_RULE_GROUPS = [_users, _team, _money, _credits, _timeline, _platforms, _project_type, _booleans, _auth, _ml, _quality,
                _compliance_region, _infra, _priorities, _extras, _name]


# ---- public API ---------------------------------------------------------------

def _set_path(d, path, value):
    cur = d
    parts = path.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def build_fields(suggestions, min_conf=0.0):
    """Collapse suggestions into a nested scenario fragment.

    Single-valued paths take the most confident value; list paths take every
    value above the threshold; low-confidence guesses stay in the suggestion
    list only.
    """
    fields = {}
    singles = {}
    lists = {}
    for s in suggestions:
        spec = FIELD_SPEC.get(s["path"])
        if spec is None or s["confidence"] < min_conf:
            continue
        if spec[0] == "list":
            vals = s["value"] if isinstance(s["value"], list) else [s["value"]]
            for v in vals:
                lists.setdefault(s["path"], {})[v] = max(lists.get(s["path"], {}).get(v, 0), s["confidence"])
        else:
            cur = singles.get(s["path"])
            if cur is None or s["confidence"] > cur["confidence"]:
                singles[s["path"]] = s
    for path, s in singles.items():
        _set_path(fields, path, s["value"])
    for path, vals in lists.items():
        keep = [v for v, conf in vals.items() if conf >= 0.4]
        if keep:
            _set_path(fields, path, sorted(keep))
    return fields


def extract_text(text):
    """Return {fields, suggestions} for a free-text project description."""
    text = (text or "").replace("’", "'").replace("‑", "-").replace("\r\n", "\n")
    c = _Collector(text)
    for group in _RULE_GROUPS:
        group(c)
    suggestions = [c.items[k] for k in c.order]
    suggestions.sort(key=lambda s: (-s["confidence"], s["path"], json.dumps(s["value"])))
    return {"fields": build_fields(suggestions, min_conf=0.4), "suggestions": suggestions}


_SECTIONS = ("project", "scale", "team", "budget", "timeline", "infrastructure", "requirements", "compliance", "priorities")


def looks_like_scenario(obj):
    return isinstance(obj, dict) and sum(1 for k in _SECTIONS if k in obj) >= 3


def find_embedded_scenario(text):
    """Return a scenario dict if the text is, or contains, a scenario JSON object."""
    s = text.strip()
    try:
        obj = json.loads(s)
        if looks_like_scenario(obj):
            return obj
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
        except ValueError:
            continue
        if looks_like_scenario(obj):
            return obj
    return None


def read_text(filename, data):
    """Return plain text from an uploaded .txt/.md/.json/.pdf/.docx file (bytes)."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    if name.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for tbl in d.tables:
            for row in tbl.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return "\n".join(parts)
    if name.endswith((".txt", ".md", ".json", ".text", "")) or True:
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeError:
                continue
    return ""


def extract_document(filename, data):
    """Read a file and return the extraction result plus any embedded scenario."""
    text = read_text(filename, data)
    scenario = find_embedded_scenario(text)
    if scenario is not None:
        return {"fields": scenario, "suggestions": [], "scenario": scenario, "text": text[:4000]}
    out = extract_text(text)
    out["scenario"] = None
    out["text"] = text[:20000]
    return out
