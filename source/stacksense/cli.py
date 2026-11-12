"""Command line: python -m stacksense run <scenario.json> [-o out.json] | run-all <dir> <outdir>"""
import argparse
import glob
import json
import os
import sys

from .engine import recommend


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(obj, path=None):
    text = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


def run_file(path):
    """Run one scenario file; unreadable JSON becomes an invalid_input result."""
    try:
        scenario = _load(path)
    except (OSError, ValueError) as e:
        scenario = "unreadable file: %s" % e
    return recommend(scenario)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stacksense", description="Deterministic stack recommendation engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="recommend a stack for one scenario file")
    r.add_argument("scenario")
    r.add_argument("-o", "--output")
    r.add_argument("--no-llm", action="store_true", help="accepted for compatibility; the engine never calls an LLM")
    ra = sub.add_parser("run-all", help="run every *.json in a folder")
    ra.add_argument("scenarios")
    ra.add_argument("outputs")
    ra.add_argument("--no-llm", action="store_true", help="accepted for compatibility; the engine never calls an LLM")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        _dump(run_file(args.scenario), args.output)
        return 0
    paths = sorted(glob.glob(os.path.join(args.scenarios, "*.json")))
    if not paths:
        sys.stderr.write("no scenario files in %s\n" % args.scenarios)
        return 1
    for path in paths:
        out = run_file(path)
        name = os.path.splitext(os.path.basename(path))[0]
        _dump(out, os.path.join(args.outputs, "%s_output.json" % name))
        print("%s: %s" % (name, out["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
