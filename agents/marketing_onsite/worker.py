"""A content worker. Three run concurrently, one task each.

A worker never touches Apollo. It reads the SOPs for the surface, works out the
exact clicks, and writes a change plan for Quenton. Applying the plan is a
separate, human-gated step — see `apply.py` — because these are production
storefronts in nine markets.

The staging rule is the spine of this module. Quenton asked on 21 Sep 2026 for
every change to go in inactive, and to proceed anyway where that is impossible.
So a plan always carries `staging`, and where the surface cannot park a change
the plan is marked `stages_live` and says so in its own first line. That is the
one thing a reader must not have to hunt for.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core import log
from agents.marketing_onsite import sops as sop_lib
from agents.marketing_onsite import surfaces, tasks

LOG = log.get("marketing_onsite")

PLAN_DIR = tasks.STORE.parent / "content_plans"


@dataclass
class Step:
    """One action in Apollo, written so a person could follow it without us."""
    order: int
    action: str
    where: str = ""
    detail: str = ""


@dataclass
class ChangePlan:
    task_id: str
    surface: str
    surface_label: str
    platform: str
    path: str
    markets: list[str]
    staging: str
    staging_control: str
    stages_live: bool
    steps: list[Step] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    copy_text: str = ""
    link: str = ""
    sops_read: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    worker: str = ""

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, indent=2, ensure_ascii=False)

    def headline(self) -> str:
        if self.stages_live:
            return (f"GOES LIVE ON SAVE — {self.surface_label} has no way to "
                    f"park a change. Proceeding per standing instruction.")
        return (f"Staged {self.staging.lower()} via {self.staging_control} — "
                f"nothing renders until you activate it.")


def _staging_steps(surface: surfaces.Surface, task: tasks.ContentTask,
                   start: int) -> tuple[list[Step], list[str]]:
    """The steps that keep the change out of customer view, plus any warnings.

    Each branch mirrors one value of `Surface.staging`; see surfaces.py for
    what each means and why the LIVE branch still proceeds.
    """
    steps: list[Step] = []
    warnings: list[str] = []
    wanted = [m.upper() for m in task.markets]

    if surface.staging == surfaces.INACTIVE:
        steps.append(Step(start, "Set Visibility to No before saving",
                          surface.path,
                          "The change is stored and invisible. Quenton flips "
                          "it to Yes when he is happy with it."))
    elif surface.staging == surfaces.SCHEDULED:
        steps.append(Step(start, "Set the date window to start in the future",
                          surface.path,
                          f"Use {surface.staging_control}. Leave the start date "
                          f"beyond today so it cannot render now; Quenton moves "
                          f"it to the real launch date on approval."))
    elif surface.staging == surfaces.MARKET:
        steps.append(Step(start, "Save with every market unticked",
                          surface.path,
                          f"Untick all of {', '.join(surfaces.MARKETS)}. The "
                          f"content exists and renders nowhere. On approval "
                          f"tick only: {', '.join(wanted) or '(markets TBC)'}."))
        warnings.append(
            "Market staging relies on the checkboxes being genuinely empty on "
            "save — confirm on screen rather than trusting the form, because a "
            "market left ticked publishes immediately.")
    else:  # LIVE
        steps.append(Step(start, "SAVE PUBLISHES IMMEDIATELY", surface.path,
                          "This surface has no visibility flag, no date window "
                          "and no market split. Per the standing instruction "
                          "the change proceeds, but it is live the moment it "
                          "is saved."))
        warnings.append(
            f"{surface.label} cannot stage a change. Saving publishes it to "
            f"customers at once. Quenton asked to proceed where staging is "
            f"impossible, so this plan does — flagging it is the whole "
            f"mitigation, so do not let it pass unread.")
    return steps, warnings


def draft(task: tasks.ContentTask, worker: str = "w1") -> ChangePlan | None:
    """Turn one task into a change plan, or send it back for input.

    Returns None when the task cannot be drafted — the task is moved to
    NEEDS_INPUT with the reasons on it, and the lead reports those to Quenton
    rather than a worker guessing.
    """
    gaps = task.blocking_gaps()
    if gaps:
        task.state = tasks.NEEDS_INPUT
        task.worker = worker
        task.notes = "Cannot draft yet:\n- " + "\n- ".join(gaps)
        tasks.save(task)
        LOG.info("content.needs_input", task=task.task_id, gaps=len(gaps))
        return None

    surface = surfaces.resolve(task.surface)
    read = sop_lib.for_surface(surface)

    steps: list[Step] = [
        Step(1, f"Open {surface.path}", surface.path,
             f"Platform: {surface.platform}."),
    ]
    n = 2
    if task.assets:
        steps.append(Step(n, "Upload the supplied asset(s)", surface.path,
                          "; ".join(Path(a).name for a in task.assets)))
        n += 1
    if task.copy_text:
        first = " ".join(task.copy_text.split())[:120]
        steps.append(Step(n, "Enter the supplied copy", surface.path, first))
        n += 1
    if task.link:
        steps.append(Step(n, "Set the destination link", surface.path, task.link))
        n += 1

    stage_steps, warnings = _staging_steps(surface, task, n)
    steps += stage_steps
    n += len(stage_steps)
    steps.append(Step(n, "Save, then screenshot the saved state",
                      surface.path,
                      "The screenshot is what Quenton reviews — it is the "
                      "evidence the change is parked, not just claimed."))

    if not read:
        warnings.append(
            f"No SOP could be read for {surface.label}. Declared pages: "
            f"{', '.join(surface.sops) or 'none declared'}. The steps below "
            f"come from the surface table alone, so check them harder.")

    plan = ChangePlan(
        task_id=task.task_id,
        surface=surface.key,
        surface_label=surface.label,
        platform=surface.platform,
        path=surface.path,
        markets=[m.upper() for m in task.markets],
        staging=surface.staging,
        staging_control=surface.staging_control,
        stages_live=surface.stages_live,
        steps=steps,
        assets=task.assets,
        copy_text=task.copy_text,
        link=task.link,
        sops_read=[s.ref for s in read],
        warnings=warnings,
        worker=worker,
    )

    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    (PLAN_DIR / f"{task.task_id}.json").write_text(plan.to_json(),
                                                   encoding="utf-8")
    task.state = tasks.DRAFTED
    task.worker = worker
    task.sops_read = plan.sops_read
    tasks.save(task)
    LOG.info("content.drafted", task=task.task_id, surface=surface.key,
             worker=worker, stages_live=plan.stages_live)
    return plan


def load_plan(task_id: str) -> ChangePlan | None:
    path = PLAN_DIR / f"{task_id}.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["steps"] = [Step(**s) for s in raw.get("steps", [])]
    return ChangePlan(**raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Draft one content change plan.")
    ap.add_argument("task_id")
    ap.add_argument("--worker", default="w1")
    args = ap.parse_args(argv)
    task = tasks.load(args.task_id)
    plan = draft(task, worker=args.worker)
    if plan is None:
        print(f"{task.task_id}: needs input\n{task.notes}")
        return 1
    print(plan.headline())
    for s in plan.steps:
        print(f"  {s.order}. {s.action}" + (f" — {s.detail}" if s.detail else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
