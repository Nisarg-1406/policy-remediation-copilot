"""
CLI entry point — python -m copilot <command> [options]

Commands:
  run    --all | --household HH-X [--ips path] [--mode replay|record|live]
  extract --household HH-X [--ips path]
  check   --household HH-X
  plan    --household HH-X
  eval    [--variance] [--synthetic-only]
  lint    --household HH-X
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent.parent
DATA_ROOT = REPO_ROOT / "data"
OUTPUT_ROOT = REPO_ROOT / "output"


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Extract → Check → Plan for one or all households."""
    import os
    from copilot.llm.client import set_llm_mode

    mode = getattr(args, "mode", None) or os.environ.get("LLM_MODE", "replay")
    set_llm_mode(mode)

    households = _resolve_households(args)

    for hh_id, ips_path in households.items():
        logger.info("=== %s: extract", hh_id)
        _run_extract(hh_id, ips_path, model=getattr(args, "model", None))

        logger.info("=== %s: check", hh_id)
        _run_check(hh_id)

        logger.info("=== %s: plan", hh_id)
        _run_plan(hh_id)

    logger.info("Done. Outputs in %s", OUTPUT_ROOT)
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    import os
    from copilot.llm.client import set_llm_mode

    mode = getattr(args, "mode", None) or os.environ.get("LLM_MODE", "replay")
    set_llm_mode(mode)

    households = _resolve_households(args)
    for hh_id, ips_path in households.items():
        _run_extract(hh_id, ips_path, model=getattr(args, "model", None))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    households = _resolve_households(args)
    for hh_id, _ in households.items():
        _run_check(hh_id)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    households = _resolve_households(args)
    for hh_id, _ in households.items():
        _run_plan(hh_id)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    import os
    from copilot.llm.client import set_llm_mode

    mode = getattr(args, "mode", None) or ("live" if getattr(args, "variance", False) else os.environ.get("LLM_MODE", "replay"))
    set_llm_mode(mode)

    from copilot.evals.runner import run_evals

    results = run_evals(
        variance=getattr(args, "variance", False),
        synthetic_only=getattr(args, "synthetic_only", False),
        model=getattr(args, "model", None),
    )
    import json

    out_dir = OUTPUT_ROOT / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(results, indent=2))
    logger.info("Eval results → %s", out_dir / "summary.json")
    _print_eval_summary(results)
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    """Run memo lint for a household — every number in memo must be in plan_facts.json."""
    from copilot.plan.memo import lint_memo

    households = _resolve_households(args)
    ok = True
    for hh_id, _ in households.items():
        facts_path = OUTPUT_ROOT / hh_id / "plan_facts.json"
        memo_path = OUTPUT_ROOT / hh_id / "plan_memo.md"
        if not facts_path.exists() or not memo_path.exists():
            logger.error("%s: plan outputs not found — run 'make run' first", hh_id)
            ok = False
            continue
        errors = lint_memo(memo_path, facts_path)
        if errors:
            for e in errors:
                logger.error("%s lint: %s", hh_id, e)
            ok = False
        else:
            logger.info("%s: memo lint PASS", hh_id)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_households(args: argparse.Namespace) -> dict[str, Path]:
    """Return {hh_id: ips_path} for the requested households."""
    import csv

    all_hh: dict[str, Path] = {}
    hh_csv = DATA_ROOT / "portfolio" / "households.csv"
    with hh_csv.open() as f:
        for row in csv.DictReader(f):
            hh_id = row["household_id"].strip()
            ips_rel = row["ips_file"].strip()
            all_hh[hh_id] = DATA_ROOT / ips_rel

    if getattr(args, "all", False):
        return all_hh

    requested_id = getattr(args, "household", None)
    if requested_id:
        ips_override = getattr(args, "ips", None)
        ips_path = Path(ips_override) if ips_override else all_hh.get(requested_id, DATA_ROOT / "ips" / f"{requested_id.lower()}.md")
        return {requested_id: ips_path}

    # default: all households
    return all_hh


def _run_extract(hh_id: str, ips_path: Path, model: Optional[str] = None) -> None:
    from copilot.extract.pipeline import extract_ips

    result = asyncio.run(extract_ips(hh_id, ips_path, model=model))
    out_path = OUTPUT_ROOT / "extraction" / f"{hh_id.lower()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    out_path.write_text(json.dumps(result, indent=2, default=str))
    logger.info("%s extraction → %s", hh_id, out_path)


def _run_check(hh_id: str) -> None:
    import json
    from copilot.check.engine import check_household

    extraction_path = OUTPUT_ROOT / "extraction" / f"{hh_id.lower()}.json"
    if not extraction_path.exists():
        raise FileNotFoundError(f"No extraction for {hh_id} — run extract first")

    extraction = json.loads(extraction_path.read_text())
    findings = check_household(hh_id, extraction)

    out_path = OUTPUT_ROOT / "checks" / f"{hh_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(findings, indent=2, default=str))
    logger.info("%s checker → %s", hh_id, out_path)


def _run_plan(hh_id: str) -> None:
    import json
    from copilot.plan.planner import plan_household

    findings_path = OUTPUT_ROOT / "checks" / f"{hh_id}.json"
    if not findings_path.exists():
        raise FileNotFoundError(f"No findings for {hh_id} — run check first")

    findings = json.loads(findings_path.read_text())
    plan_result = plan_household(hh_id, findings)

    out_dir = OUTPUT_ROOT / hh_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "trades.csv").write_text(plan_result["trades_csv"])
    (out_dir / "plan_memo.md").write_text(plan_result["memo_md"])
    (out_dir / "plan_facts.json").write_text(json.dumps(plan_result["facts"], indent=2, default=str))
    logger.info("%s plan → %s", hh_id, out_dir)


def _print_eval_summary(results: dict) -> None:
    print("\n=== EVAL METRICS ===")
    for metric, value in results.get("metrics", {}).items():
        print(f"  {metric}: {value}")
    print("\n=== GATES ===")
    for gate, passed in results.get("gates", {}).items():
        status = "✓" if passed else "✗"
        print(f"  {status} {gate}: {'PASS' if passed else 'FAIL'}")
    if results.get("all_gates_pass"):
        print("\n✓ All gates PASS")
    else:
        print("\n✗ Some gates FAILED (expected benchmark gaps documented in README) — see output/evals/")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="copilot",
        description="Policy Remediation Copilot — Cedar Ridge Wealth Partners",
    )
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Extract + check + plan")
    p_run.add_argument("--all", action="store_true", help="Run all households")
    p_run.add_argument("--household", help="Household ID (e.g. HH-CHEN)")
    p_run.add_argument("--ips", help="Path to IPS file (overrides households.csv)")
    p_run.add_argument("--mode", choices=["replay", "record", "live"], help="LLM mode")
    p_run.add_argument("--model", help="LLM model (e.g. gpt-5.4-nano, gpt-4o)")
    p_run.set_defaults(func=cmd_run)

    # extract
    p_ext = sub.add_parser("extract", help="Run extraction only")
    p_ext.add_argument("--all", action="store_true")
    p_ext.add_argument("--household")
    p_ext.add_argument("--ips")
    p_ext.add_argument("--mode", choices=["replay", "record", "live"])
    p_ext.add_argument("--model", help="LLM model (e.g. gpt-5.4-nano, gpt-4o)")
    p_ext.set_defaults(func=cmd_extract)

    # check
    p_chk = sub.add_parser("check", help="Run checker only")
    p_chk.add_argument("--all", action="store_true")
    p_chk.add_argument("--household")
    p_chk.set_defaults(func=cmd_check)

    # plan
    p_plan = sub.add_parser("plan", help="Run planner only")
    p_plan.add_argument("--all", action="store_true")
    p_plan.add_argument("--household")
    p_plan.set_defaults(func=cmd_plan)

    # eval
    p_eval = sub.add_parser("eval", help="Run extraction evals")
    p_eval.add_argument("--variance", action="store_true", help="Run N-run variance (live mode)")
    p_eval.add_argument("--synthetic-only", action="store_true")
    p_eval.add_argument("--mode", choices=["replay", "record", "live"])
    p_eval.add_argument("--model", help="LLM model (e.g. gpt-5.4-nano, gpt-4o)")
    p_eval.set_defaults(func=cmd_eval)

    # lint
    p_lint = sub.add_parser("lint", help="Lint plan memo")
    p_lint.add_argument("--all", action="store_true")
    p_lint.add_argument("--household")
    p_lint.set_defaults(func=cmd_lint)

    args = parser.parse_args()
    _setup_logging(args.verbose)

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
