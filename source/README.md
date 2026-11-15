# ArchLens (StackSense engine)

## 1. Name and overview

Repository: https://github.com/SantoshReddy0603/ArchLens

ArchLens turns a project scenario (a JSON file, or a free-text description reviewed in the UI) into a justified technology stack: frontend, backend, database, hosting and supporting services, plus an architecture style, a monthly cost band, warnings, assumptions and a full decision trace.

The stack is chosen only by a deterministic rule and score engine over a hand-written knowledge base. An optional LLM can suggest form values from a free-text description and reword the summary. It never picks, ranks or changes a stack.

## 2. Architecture

```
 description / .txt / .pdf / .docx            scenario JSON
              |                                    |
              v                                    |
   extract.py (regex + lexicon,                    |
   optional llm.py suggestions)                    |
              |  suggestions the user confirms     |
              v                                    v
        +--------------------- web/app.py (FastAPI) ---------------------+
        |  /api/extract  /api/validate  /api/recommend  /api/narrate  /api/report.pdf|
        +--------------------------------+-------------------------------+
                                         |
                                         v
   validate.py -> derive.py -> constraints.py -> stacks.py -> scoring.py
   (normalize,   (profile:     (hard filters    (search over  (Q, F,
    errors,       weights,      per layer)       compatible    penalties)
    assumptions)  needs)                         combinations)
                                         |
                       supporting.py -> cost.py -> conflicts.py -> explain.py -> robust.py
                                         |
                                         v
                  result JSON  ->  UI (web/)  |  PDF (report.py)  |  CLI
```

Data flow: input is validated and normalized, a derived profile (scale, effort, weights, constraints) is computed, each layer is filtered by hard constraints, compatible stacks are enumerated and scored, the best stack gets supporting services and a cost band, conflicts are detected, and the explanation and trace are assembled. The engine is a pure function: `recommend(scenario) -> dict`.

| Module | Role |
|---|---|
| `stacksense/validate.py` | Schema checks, defaults, `validation_errors` and `assumptions`. Bad input gives `status: invalid_input`, never a crash |
| `stacksense/derive.py` | Builds the profile: capacity, build effort, skill fit, criterion weights |
| `stacksense/constraints.py`, `stacks.py` | Per-layer elimination with reasons, then search over compatible combinations |
| `stacksense/scoring.py` | Quality, fit and penalty terms, score formula |
| `stacksense/supporting.py`, `cost.py` | Supporting services (auth, payments, search and so on) and the USD cost band |
| `stacksense/conflicts.py` | Contradiction and risk warnings |
| `stacksense/explain.py` | Per-component justification, alternatives, trade-offs |
| `stacksense/robust.py` | Sensitivity checks on the pick, directional trade-off options, and single-change repairs for infeasible input |
| `stacksense/kb/*.json`, `kb.py` | Knowledge base: 24 frontends, 16 backends, 16 databases, 27 hosts, 122 supporting services, 20 integrations, 21 deprecations, each with scores, notes and source links; `offerings.json` names the concrete managed product for an engine on each cloud |
| `stacksense/extract.py` | Free text or file to field suggestions with evidence and confidence |
| `stacksense/llm.py` | Optional Gemini calls (suggestions, rewording) behind one network function |
| `stacksense/report.py` | PDF export |
| `stacksense/web/app.py`, `web/` | HTTP API and the single-page UI (start, review, result views) |
| `stacksense/cli.py` | Command line runner |

## 3. Technology

| Item | Version |
|---|---|
| Python | 3.12 (3.10 or newer should work) |
| FastAPI | 0.162.2 |
| Uvicorn | 0.54.0 |
| python-multipart | 0.0.33 |
| pypdf | 7.0.1 |
| python-docx | 1.2.0 |
| reportlab | 5.1.0 |
| Tests only: pytest 9.2.0, httpx 0.28.1 | |
| Frontend | Plain HTML, CSS and JavaScript, no build step, no CDN |
| Fonts | Instrument Serif and Hanken Grotesk, bundled in `web/fonts` |

## 4. Prerequisites

- Python 3.10 or newer and pip
- A modern browser for the UI
- Internet is not needed. It is used only if you enable the optional Gemini features.

## 5. Setup and run

Windows (PowerShell):

```
cd source
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
py run.py
```

macOS and Linux:

```
cd source
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:8000. Optional settings go in a `.env` file (copy `.env.example`): `STACKSENSE_LLM`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `HOST`, `PORT`.

## 6. Running a scenario

CLI, one file or a whole folder (run from `source/`):

```
python -m stacksense run scenarios/P2.json -o ../outputs/P2_output.json
python -m stacksense run-all scenarios ../outputs
python -m stacksense run-all scenarios/edge ../outputs/edge
```

`run-all` writes `<name>_output.json` into the output folder. This repository keeps the results for the six public scenarios in `outputs/` next to `source/`, and the edge cases in `outputs/edge/`. Without `-o`, `run` prints the JSON to stdout.

UI: on the start page pick a sample, upload a scenario `.json`, or paste or upload a description (.txt, .pdf, .docx). Review and edit the form, press Evaluate, then read the result, open "Inspect the calculations", and export JSON or PDF.

## 7. Switching the LLM off

- Environment: `STACKSENSE_LLM=off` (shell or `.env`). A missing `GEMINI_API_KEY` has the same effect.
- CLI: `--no-llm` is accepted; the CLI never calls an LLM.
- UI: the "AI wording" switch in the page header.
- API: `"options": {"llm": false}` on `/api/recommend`, `llm=false` on `/api/extract`, or `options.llm=false` on `/api/narrate` (the optional wording call, made after the result is shown).

The recommended stack, scores and trace are identical with the LLM on or off.

## 8. Project structure

```
ArchLens/
  outputs/                 P1_output.json .. P6_output.json
    edge/                  outputs for the edge scenarios
  source/
    README.md  requirements.txt  requirements-dev.txt  .env.example  run.py
    scenarios/             P1..P6.json
      edge/                E1..E11: air-gapped, conflicting constraints, hard budget too low,
                           end-of-life systems, invalid input, sparse input, skill mismatch, global scale,
                           high-traffic static site, small internal tool, HIPAA in the US
    stacksense/            engine, extractor, LLM helper, report, CLI
      kb/                  knowledge base JSON
      web/app.py           FastAPI app
    web/                   index.html, css/, js/, fonts/, favicon.svg
    tests/                 test_engine.py, test_extract.py, test_web.py
```

## 9. Credits

- FastAPI, Starlette, Uvicorn, python-multipart: web serving (MIT, BSD)
- pypdf and python-docx: reading uploaded PDF and Word descriptions (BSD, MIT)
- reportlab: PDF report (BSD)
- pytest and httpx: tests (MIT, BSD)
- Instrument Serif (Rodrigo Fuenzalida, Jordan Egstad) and Hanken Grotesk (Hanken Design Co.): fonts, SIL Open Font License 1.1, licence file in `web/fonts/OFL.txt`
- Google Gemini API: optional, used only when a key is set and the LLM is not switched off
- Dataset: none. The knowledge base is written by hand from vendors' public documentation and pricing pages; each entry lists its source URLs and a verification date.

## Scoring model

```
score = 100 * (0.55 * Q + 0.45 * F) - penalties
```

- Q (quality): per-criterion scores (cost, time to market, scalability, performance, maintainability, security; each 0-10 from the KB with a stated reason) averaged over layers with weights backend 0.30, hosting 0.30, database 0.25, frontend 0.15 (static sites shift weight to frontend and hosting). Criterion weights come from the project's priorities and are adjusted by context (time pressure, budget tightness, compliance, scale).
- F (fit), weights: skill 0.28, capability coverage 0.22, budget 0.18, ops load 0.14, compatibility 0.10, project-type archetype 0.08.
- Budget fit is 1 well under budget, 0.7 at the budget, then falls steeply. When no compatible stack fits the budget, it is measured against the cheapest compatible stack plus 25%, so a $0 budget still prefers cheaper stacks instead of treating them all as equally unaffordable.
- Penalties (points): over-engineering, self-running a business database on a cloud VM at a 99.9%+ target, a stated mobile preference that is not met, under-provisioning, lock-in, hiring difficulty, immaturity, availability gap, missing soft certifications, poor SEO, a second codebase, cold start, data-model mismatch (document store for relational records, general store for large telemetry or analytics, time-series store for a general app), and an embedded single-node database for business records. Reuse of existing systems is a small bonus.
- Availability is computed for the whole serving path: the host SLA (single-instance or redundant), lowered for self-managed hosts by the team's missing operations capacity, and capped by the SLA of a BaaS backend or managed database.
- Hard constraints remove candidates before scoring (data residency, platform support, runtime and host compatibility, on-premise rules, hard budget, game-client rules). Every elimination is recorded with its reason.
- Near ties: among stacks within 0.75 points of the best score, the cheapest (within 6%) wins, then lower complexity, lower operations load and ids. Stated cloud credits count in that cost comparison.
- Every term is a smooth function of the profile, so small input changes move scores a little.
- When a heavier setup trails mainly because of its complexity and operations load for this team, an `OVERENGINEERING_AVOIDED` note names it and the points involved.
- Placement: when data residency is set (or GDPR applies with no residency), the database and host explanations name a concrete region for the chosen provider, for example Mumbai (ap-south-1) or Frankfurt (fra).
- The project description is scanned with fixed patterns for low-end devices, weak networks, location data and UPI. These shift criterion weights and raise specific warnings (`LOW_END_DEVICES`, `WEAK_NETWORK`, `LOCATION_DATA`). Tight latency with live updates raises `REALTIME_LATENCY`, and offline edits on shared records raise `OFFLINE_CONFLICTS`.

- Approval steps in the description (leave requests, sign-offs, expense claims) add a notification email service; a double-entry or financial ledger with payments raises `LEDGER_INTEGRITY` (append-only entries, balanced rows in one transaction, integer minor units); location data on a PostgreSQL-family database adds PostGIS as a geospatial layer.

- Hard budget: a stack is kept only if its core layers plus the cheapest required supporting services fit. After supporting services are chosen, the complete plan is checked again; if it is over, cheaper services are swapped in (each swap is shown as an alternative with the money saved) or the next stack is tried, and if nothing fits the result is `infeasible` with `BUDGET_INFEASIBLE` and the single changes that would fix it.
- When a layer is covered by the core stack (for example PostgreSQL full-text search or Supabase Auth), dedicated services such as Typesense or Auth0 are still listed as alternatives, with their cost and when they become worth it. A static site that needs search gets Pagefind, an index built with the site and served from the same static host. On-premise and air-gapped deployments send mail through a self-run Postfix relay, which is never offered on cloud hosting.
- `architecture` explains the architecture style: why it fits (team size, feature areas, host, scale), the inputs that drove it, and why the neighbouring styles (monolith, modular monolith, serverless, microservices) were not chosen.
- India-specific notes: `CERT_IN_LOGS` (180-day log retention in India under the CERT-In directions) when residency is India or DPDP/RBI apply, and `REGIONAL_NETWORK_BLOCK` when a Supabase stack serves Indian users (shared-domain ISP block in February-March 2026; use a custom domain).
- A required certification that the chosen services do not cover is a critical `CERT_GAP`; data residency set to `other` gives `RESIDENCY_UNVERIFIED`, since no provider region can be confirmed.

Each component also carries `kb_id`, up to three `sources` (official documentation links), and for the core layers a `confidence` (`high`, `medium` or `low`) with `margin_points`, the score lead over the best stack that uses a different option for that layer. The database names the concrete managed product on the chosen cloud (`engine` and `offering`, for example PostgreSQL run as Amazon RDS for PostgreSQL on AWS, or Cloud SQL on Google Cloud) and its region; this naming never changes which stack is picked. `estimated_monthly_cost_usd.breakdown` lists the cost of every core layer and supporting service, and its rows add up to the band, give or take rounding.

The result carries `ranking` (top 10 stacks), `score` (total, Q, F, penalties, fit terms, criteria) and `decision_trace` (weights, layer weights, eliminated candidates per layer, search counts, formula). The UI and the PDF both show them.

## How firm the pick is, and what else to consider

- `robustness`: the winner is re-ranked with each priority doubled and halved (12 checks) and with the budget halved and doubled. Only stacks that could overtake it are re-scored, using an exact bound on how far a weight change can move a score. The verdict is `stable`, `mostly stable` or `sensitive`, with the margin to the runner-up and the stack each failing check switches to.
- `trade_off_options`: for each direction (spend less, less operations work, more headroom, faster build, closer to the team's skills, less lock-in) the best stack within 12 points that does clearly better on it, with what it gains and what it gives up in points. When nothing nearby does better, the option says so.
- `repairs` (infeasible results only): single relaxations are tried one at a time (residency, a compliance regime, cloud preference, deployment model, hard budget) and every one that makes a stack possible is listed with that stack and its cost, also as `FEASIBLE_IF_CHANGED` notes.

The UI and PDF show all three. `scenarios/edge/` holds eleven hand-built edge cases that exercise these paths.

## Determinism guarantees

- `recommend` uses no clock, randomness, network or environment values.
- Ties are broken by sorting on stable ids; dictionaries are built and emitted in a fixed order.
- Floats are rounded before output, so repeated runs and different `PYTHONHASHSEED` values give byte-identical JSON.
- The LLM modules are not imported by the engine, so toggling the LLM cannot change a stack.

## Extra inputs and what each changes

All sit under `extra` and are optional.

| Field | Values | Effect |
|---|---|---|
| `learning_appetite` | low, medium, high | Tolerance for unfamiliar tech in skill fit |
| `lock_in_tolerance` | low, medium, high | Size of the lock-in penalty |
| `user_geography` | single_city, country, multi_region, global | CDN need and latency warnings; an explicit multi_region or global raises the performance weight |
| `data_sensitivity` | public, internal, personal, financial, health | Security weight (lower for public, higher for financial or health) and `SENSITIVE_DATA_UNREGULATED` when health or financial data has no matching compliance listed |
| `budget_strictness` | flexible, firm, hard | How strongly the budget shapes fit; `hard` removes over-budget stacks |
| `hiring_plan` | none, slow, aggressive | Talent-pool penalty and maintainability weight |
| `mobile_preference` | no_preference, native, cross_platform, pwa | `pwa` serves Android and iOS users from one installable web app; `native` enumerates separate Kotlin and Swift clients and penalises cross-platform ones (and the reverse for `cross_platform`). When the preference loses on timeline or skills, `PREFERENCE_OUTWEIGHED` says by how much |
| `existing_licenses` | list of names | Reuse bonus for stacks that build on them (for example `windows_server` moves an on-premise stack to IIS and ASP.NET Core) and a `LICENCE_REUSED` note |
| `expected_rps` | number | Replaces the peak load estimated from users |
| `cloud_credits_usd` | number (total USD) | With `aws_credits`, `gcp_credits` or `azure_credits` listed, the credits are spread over the first year when near-tied stacks are compared on cost, and the result reports which line items they cover, how many months they last and the first-year average bill (`estimated_monthly_cost_usd.credits`, `CLOUD_CREDITS`) |

When `extra` is missing, neutral defaults are used and listed in `assumptions`.

## LLM policy declaration

- The LLM is used for two things only: suggesting values from a free-text description (shown as suggestions the user confirms, confidence capped at 0.7, validated against the schema) and rewording the already produced summary.
- The LLM never selects, ranks, scores or filters technologies.
- All network access goes through one function, `llm._post`. A narrative that names a product not present in the result is rejected.
- It is off when `STACKSENSE_LLM=off`, when no key is set, or when the request or toggle says so. The stack is the same in every case.

## How extraction works without an LLM

`extract.py` applies regex and lexicon rules to the text: numbers with units (users, budget in USD or INR, team size, weeks and months), platform, language, compliance and feature keywords, and priority cues. Each suggestion keeps its evidence snippet and a confidence. Rules always win over model suggestions. A scenario JSON embedded in a document is detected and loaded directly. Nothing is applied until the user accepts it in the review step.

## Tests

From `source/`:

```
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite covers the engine, the extractor, the API and the PDF, and includes checks that no network call is made with the LLM off.
