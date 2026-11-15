# ArchLens

Deterministic tech-stack recommender for the StackSense challenge. It turns a project scenario into a justified stack with cost, warnings and a full decision trace.

![ArchLens demo: describe a project, review the extracted values, evaluate, read the result and the calculations](source/docs/demo.gif)

Live demo: https://archlens-sxie.onrender.com (free hosting, so the first load after a quiet period can take about a minute). The local setup below is the reference.

## Run it

Python 3.10 or newer. About two minutes on a normal connection; no API key needed.

```
cd source
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python run.py                          # Windows: .venv\Scripts\python run.py
```

Open http://127.0.0.1:8000. To run a scenario from the command line: `python -m stacksense run scenarios/P2.json -o ../outputs/P2_output.json`.

## Contents

- `source/`: the application, knowledge base, scenarios, benchmark and tests. Setup, architecture, usage and design are in [source/README.md](source/README.md).
- `outputs/`: generated results for the public scenarios (`P1`-`P6`) and the edge cases (`outputs/edge/`).

![ArchLens architecture](source/docs/architecture.svg)
