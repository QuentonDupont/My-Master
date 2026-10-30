"""Agents, flows and triggers — the runtime every scheduled action goes through.

The system used to be four standing "department" agents, each owning many
actions and chained together by shell scripts. It is now many small agents,
each doing exactly one thing, composed into flows, each flow started by a
declared trigger:

    Agent    one action. A name, a function `run(ctx)`, and the side-effect class
             it is allowed: read, local, jira_write or email.
    Flow     an ordered chain of agents (or a ForEach fanning a sub-flow out over
             items), with a per-step failure policy.
    Trigger  when a flow runs: Every(seconds), Daily(at, weekdays), On(event),
             or Human() — only on an explicit confirm.

The guardrails of CLAUDE.md are enforced here, at definition time, so a flow
that would break them cannot even be constructed:

  * an agent may declare `jira_write` only if it is one of the sanctioned
    write paths — `execute_proposal` (invariant 1), `mirror_parent_status`
    (the status-mirroring exception) and `guard_automation_usage` (switches
    [PM-Auto] automation rules off near the usage limit);
  * a flow containing `execute_proposal` must be Human-triggered only. Approval
    queues it; nothing runs it unattended.

Agents communicate through the flow's Context (`ctx.data`), never through
globals. An agent ends its flow early, successfully, with `ctx.stop(reason)` —
e.g. a gate that has decided the ticket's outcome.
"""
from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from core import log

LOG = log.get("flow")

# -- side-effect classes -----------------------------------------------------
READ = "read"            # reads Jira / corpus / files; writes nothing
LOCAL = "local"          # writes the ledger, review/, knowledge/ or logs/ only
JIRA_WRITE = "jira_write"
EMAIL = "email"
EFFECTS = (READ, LOCAL, JIRA_WRITE, EMAIL)

#: The only agents allowed to write to Jira. Invariant 1 and its exceptions.
#: guard_automation_usage only ever switches [PM-Auto] automation rules OFF (30 Oct 2026).
SANCTIONED_JIRA_WRITERS = frozenset({"execute_proposal", "mirror_parent_status",
                                     "guard_automation_usage"})
#: Of those, the ones a scheduled flow may run unattended.
UNATTENDED_JIRA_WRITERS = frozenset({"mirror_parent_status", "guard_automation_usage"})
#: Agents that may only ever run under a Human trigger.
HUMAN_ONLY = frozenset({"execute_proposal"})


class FlowError(RuntimeError):
    pass


class Stop(Exception):
    """Raised by ctx.stop(): the flow ends early, and that is a success."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# -- triggers -----------------------------------------------------------------
@dataclass(frozen=True)
class Every:
    seconds: int

    def describe(self) -> str:
        s = self.seconds
        return f"every {s // 3600}h" if s % 3600 == 0 else (
            f"every {s // 60}m" if s % 60 == 0 else f"every {s}s")


@dataclass(frozen=True)
class Daily:
    at: str                              # "HH:MM", machine local time
    weekdays: tuple[int, ...] | None = None  # 0 = Monday; None = every day
    #: A slot missed by more than this (machine asleep, scheduler just
    #: installed) is skipped, not run late — a 09:00 email should not arrive
    #: at 16:00 because that is when the laptop woke up.
    grace_seconds: int = 3 * 3600

    def slot(self, now: dt.datetime) -> dt.datetime | None:
        """Today's scheduled instant, if it has passed and is still in grace."""
        hh, mm = (int(x) for x in self.at.split(":"))
        if self.weekdays is not None and now.weekday() not in self.weekdays:
            return None
        slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if slot > now or (now - slot).total_seconds() > self.grace_seconds:
            return None
        return slot

    def describe(self) -> str:
        names = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        when = ("daily" if self.weekdays is None else
                "on " + ",".join(names[d] for d in self.weekdays))
        return f"{when} at {self.at}"


@dataclass(frozen=True)
class On:
    event: str  # an event name, or a prefix ending in "." (e.g. "ticket.")

    def matches(self, name: str) -> bool:
        return name.startswith(self.event) if self.event.endswith(".") else name == self.event

    def describe(self) -> str:
        return f"on {self.event}"


@dataclass(frozen=True)
class Human:
    def describe(self) -> str:
        return "human confirm"


Trigger = Any  # Every | Daily | On | Human


# -- agents and steps --------------------------------------------------------
@dataclass(frozen=True)
class Agent:
    name: str
    run: Callable[["Context"], Any]
    effect: str = LOCAL
    doc: str = ""

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise FlowError(f"agent {self.name}: unknown effect {self.effect!r}")
        if self.effect == JIRA_WRITE and self.name not in SANCTIONED_JIRA_WRITERS:
            raise FlowError(
                f"agent {self.name} declares jira_write, but the only Jira write "
                f"paths are {sorted(SANCTIONED_JIRA_WRITERS)} (CLAUDE.md invariant 1)")


def agent(name: str, effect: str = LOCAL) -> Callable[[Callable], Agent]:
    """Decorator: turn `def fn(ctx)` into an Agent, its docstring as its doc."""
    def wrap(fn: Callable[["Context"], Any]) -> Agent:
        doc = (fn.__doc__ or "").strip().splitlines()
        return Agent(name, fn, effect, doc[0] if doc else "")
    return wrap


@dataclass(frozen=True)
class Step:
    agent: Agent
    on_error: str = "stop"  # "stop" ends the flow as failed; "continue" records it

    def __post_init__(self) -> None:
        if self.on_error not in ("stop", "continue"):
            raise FlowError(f"step {self.agent.name}: on_error must be stop|continue")


@dataclass(frozen=True)
class ForEach:
    """Fan a sub-flow out over `ctx.data[items]`, up to `max_workers` at once.

    `seed(ctx, item)` builds each sub-flow's starting data. Each sub-run's
    result data is collected, in completion order, into `ctx.data[collect]`.
    One item failing never stops the others.
    """
    name: str
    items: str
    flow: "Flow"
    seed: Callable[["Context", Any], dict]
    collect: str = "results"
    max_workers: int = 1
    #: Optional ctx key that, when set, overrides max_workers for this run.
    workers_from: str | None = None

    @property
    def agents(self) -> list[Agent]:
        return self.flow.agents


def _as_step(s) -> Step | ForEach:
    if isinstance(s, (Step, ForEach)):
        return s
    if isinstance(s, Agent):
        return Step(s)
    raise FlowError(f"not a step: {s!r}")


# -- context and results -----------------------------------------------------
class Context:
    def __init__(self, flow: str, *, trigger: str = "manual", execute: bool = False,
                 data: dict | None = None, events: list[dict] | None = None) -> None:
        self.flow = flow
        self.trigger = trigger
        self.execute = execute       # writes for real; dry-run otherwise
        self.data: dict[str, Any] = dict(data or {})
        self.events: list[dict] = list(events or [])

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def stop(self, reason: str) -> None:
        raise Stop(reason)


@dataclass
class StepResult:
    agent: str
    ok: bool
    ms: int
    error: str | None = None

    def to_dict(self) -> dict:
        d = {"agent": self.agent, "ok": self.ok, "ms": self.ms}
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class RunResult:
    flow: str
    ok: bool
    trigger: str
    steps: list[StepResult] = field(default_factory=list)
    stopped: str | None = None
    error: str | None = None
    data: dict = field(default_factory=dict)
    #: The exception that stopped the flow, so a wrapper can re-raise it as is.
    exc: BaseException | None = field(default=None, repr=False)

    def raise_for_error(self) -> None:
        """Re-raise a failure that stopped the flow. A step that failed with
        on_error="continue" did not stop it: callers read `errors` for those."""
        if self.exc is not None:
            raise self.exc
        if self.error:
            raise RuntimeError(self.error)

    @property
    def errors(self) -> list[str]:
        return [f"{s.agent}: {s.error}" for s in self.steps if not s.ok]

    def summary(self) -> dict:
        out = {"flow": self.flow, "ok": self.ok, "trigger": self.trigger,
               "steps": [s.to_dict() for s in self.steps]}
        if self.stopped:
            out["stopped"] = self.stopped
        if self.error:
            out["error"] = self.error
        return out


# -- flows ---------------------------------------------------------------------
@dataclass
class Flow:
    name: str
    steps: list
    triggers: list = field(default_factory=list)
    doc: str = ""
    #: Scheduled/event runs write for real (execute=True). Only for flows whose
    #: writes are already sanctioned to happen unattended: the status mirror and
    #: the daily report email. Everything else is dry-run unless --execute.
    live_when_scheduled: bool = False
    #: Called with (ctx, exc) when a step with on_error="stop" fails.
    on_failure: Callable[[Context, BaseException], None] | None = None
    #: A run older than this is presumed dead and its lock can be taken over.
    timeout_seconds: int = 1800
    #: Always called with ctx after the last step — success, stop or failure.
    #: Where an agent that opened a connection gets it closed.
    cleanup: Callable[[Context], None] | None = None

    def __post_init__(self) -> None:
        self.steps = [_as_step(s) for s in self.steps]
        names = [a.name for a in self.agents]
        human_only = HUMAN_ONLY & set(names)
        if human_only and any(not isinstance(t, Human) for t in self.triggers):
            raise FlowError(
                f"flow {self.name} contains {sorted(human_only)}, which may only run "
                f"on a Human trigger — approval queues it, nothing runs it unattended")
        if self.live_when_scheduled:
            writers = {a.name for a in self.agents if a.effect == JIRA_WRITE}
            if writers - UNATTENDED_JIRA_WRITERS:
                raise FlowError(f"flow {self.name} cannot write to Jira unattended")

    @property
    def agents(self) -> list[Agent]:
        out: list[Agent] = []
        for s in self.steps:
            out.extend(s.agents if isinstance(s, ForEach) else [s.agent])
        return out

    @property
    def effects(self) -> set[str]:
        return {a.effect for a in self.agents}

    def describe(self) -> dict:
        def step_desc(s) -> Any:
            if isinstance(s, ForEach):
                return {"for_each": s.items, "workers": s.max_workers,
                        "flow": s.flow.name,
                        "agents": [step_desc(x) for x in s.flow.steps]}
            d = f"{s.agent.name} [{s.agent.effect}]"
            return d + (" (continue on error)" if s.on_error == "continue" else "")
        return {"flow": self.name, "doc": self.doc,
                "triggers": [t.describe() for t in self.triggers] or ["called by another flow"],
                "live_when_scheduled": self.live_when_scheduled,
                "steps": [step_desc(s) for s in self.steps]}

    # -- execution ---------------------------------------------------------
    def run(self, ctx: Context | None = None, **data: Any) -> RunResult:
        ctx = ctx or Context(self.name)
        ctx.data.update(data)
        result = RunResult(self.name, True, ctx.trigger, data=ctx.data)
        LOG.info("flow.start", flow=self.name, trigger=ctx.trigger, execute=ctx.execute)
        for step in self.steps:
            started = time.monotonic()
            name = step.name if isinstance(step, ForEach) else step.agent.name
            try:
                if isinstance(step, ForEach):
                    self._fan_out(step, ctx)
                else:
                    step.agent.run(ctx)
            except Stop as stop:
                result.steps.append(StepResult(name, True, _ms(started)))
                result.stopped = f"{name}: {stop.reason}"
                break
            except Exception as exc:  # one agent failing is reported, never hidden
                result.steps.append(StepResult(name, False, _ms(started), str(exc)[:500]))
                LOG.error("flow.step_failed", flow=self.name, agent=name, error=str(exc)[:500])
                if isinstance(step, Step) and step.on_error == "continue":
                    result.ok = False
                    continue
                result.ok = False
                result.error = f"{name}: {exc}"
                result.exc = exc
                if self.on_failure:
                    try:
                        self.on_failure(ctx, exc)
                    except Exception as hook_exc:  # pragma: no cover - defensive
                        LOG.error("flow.on_failure_failed", flow=self.name,
                                  error=str(hook_exc)[:500])
                break
            else:
                result.steps.append(StepResult(name, True, _ms(started)))
        if self.cleanup:
            try:
                self.cleanup(ctx)
            except Exception as exc:  # pragma: no cover - defensive
                LOG.error("flow.cleanup_failed", flow=self.name, error=str(exc)[:500])
        LOG.info("flow.done", flow=self.name, ok=result.ok, stopped=result.stopped,
                 errors=result.errors)
        return result

    def _fan_out(self, step: ForEach, ctx: Context) -> None:
        items = list(ctx.get(step.items) or [])
        collected: list[dict] = []
        ctx[step.collect] = collected
        if not items:
            return

        def one(item: Any) -> dict:
            sub = Context(step.flow.name, trigger=f"{ctx.flow}/{step.name}",
                          execute=ctx.execute, data=step.seed(ctx, item))
            res = step.flow.run(sub)
            return dict(res.data, _ok=res.ok, _stopped=res.stopped, _error=res.error)

        width = ctx.get(step.workers_from) if step.workers_from else None
        workers = max(1, min(int(width or step.max_workers), len(items)))
        if workers == 1:
            collected.extend(one(i) for i in items)
            return
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix=f"{self.name}-{step.name}") as pool:
            futures = [pool.submit(one, i) for i in items]
            for fut in as_completed(futures):
                collected.append(fut.result())


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
