"""CLI for the flows. See flows/__init__.py for the commands."""
from __future__ import annotations

import argparse
import json
import sys

import flows
from core import scheduler
from core.flow import FlowError


def _print_result(result) -> None:
    report = result.data.get("report")
    if isinstance(report, str):
        print(report)
    elif report is not None:
        print(json.dumps(report, indent=2, default=str))
    print(json.dumps(result.summary(), indent=2, default=str), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m flows")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="every flow, its triggers and agents")
    p_show = sub.add_parser("show", help="one flow in full")
    p_show.add_argument("flow")
    p_run = sub.add_parser("run", help="run one flow now")
    p_run.add_argument("flow")
    p_run.add_argument("--execute", action="store_true",
                       help="write for real (default is dry-run)")
    p_run.add_argument("--confirm", action="store_true",
                       help="required for human-triggered flows")
    p_run.add_argument("--trigger", default="manual",
                       choices=["manual", "schedule", "event"])
    p_run.add_argument("--from-tick", action="store_true", help=argparse.SUPPRESS)
    p_run.add_argument("--data", default="{}", help="JSON object of starting data")
    sub.add_parser("tick", help="start every due flow (launchd runs this)")
    sub.add_parser("status", help="last run, lock and pending events per flow")
    p_runs = sub.add_parser("runs", help="recent run history")
    p_runs.add_argument("flow", nargs="?")
    p_runs.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for f in flows.registry().values():
            d = f.describe()
            print(f"{f.name:<14} {', '.join(d['triggers'])}")
            print(f"{'':<14} {f.doc}")
            print(f"{'':<14} {' -> '.join(a.name for a in f.agents)}\n")
        return 0
    if args.cmd == "show":
        try:
            print(json.dumps(flows.get(args.flow).describe(), indent=2))
        except KeyError as exc:
            print(f"error: {exc.args[0]}", file=sys.stderr)
            return 2
        return 0
    if args.cmd == "tick":
        print(json.dumps(scheduler.tick(flows.registry()), indent=2))
        return 0
    if args.cmd == "status":
        print(json.dumps(scheduler.status(flows.registry()), indent=2))
        return 0
    if args.cmd == "runs":
        with scheduler.Store() as store:
            for r in store.runs(args.flow, args.limit):
                print(json.dumps(r))
        return 0

    try:
        flow = flows.get(args.flow)
        data = json.loads(args.data)
        if not isinstance(data, dict):
            raise ValueError("--data must be a JSON object")
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        print(f"error: bad --data: {exc}", file=sys.stderr)
        return 2
    try:
        result = scheduler.run_flow(
            flow, trigger=args.trigger, execute=args.execute,
            confirm=args.confirm, notify=args.from_tick, data=data)
    except FlowError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    _print_result(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
