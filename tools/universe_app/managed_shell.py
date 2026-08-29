"""Supervisor-owned managed cmd shell lifecycle for one terminal.

One managed path per terminal:

```text
Session Anchor -> Supervisor-owned headless ConPTY cmd.exe -> provider CLI
```

There is no separate launcher service and no second handshake path. The Session
Anchor is resolved *before* the shell is spawned, so a PTY is always an
attachment to an already-opaque Anchor rather than the thing that invents one.

State is determined from the owned process tree plus the SessionStart hook
receipt. It is never inferred from prompt text or PTY byte activity, because a
CLI can print a prompt-looking string before it is attached and can fall silent
while perfectly healthy. A bare PID is likewise insufficient: Windows reuses
PIDs, so every identity comparison pairs the PID with its process start time.

Nothing here grants Mode currentness. Liveness is an observation about a
process tree; Authority, Execution Assignment, and Mode Current Anchor
currentness are decided elsewhere and are never derived from these states.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence


MANAGED_SHELL_SCHEMA = "universe.managed-shell-state.v1"

# Lifecycle states for one managed cmd shell and the CLI it hosts.
SHELL_READY = "SHELL_READY"
CLI_STARTING = "CLI_STARTING"
CLI_ATTACHED = "CLI_ATTACHED"
CLI_RUNNING = "CLI_RUNNING"
SHELL_IDLE = "SHELL_IDLE"
CLI_START_FAILED = "CLI_START_FAILED"
HOOK_TIMEOUT = "HOOK_TIMEOUT"
PTY_UNRESPONSIVE = "PTY_UNRESPONSIVE"
SHELL_EXITED = "SHELL_EXITED"
# Reserved, and deliberately NOT derived at present.  The only I/O signal this
# Host has is the pump's own read loop, which cannot support the claim: if a
# read hangs the same thread stops sampling, and if it returns on timeout the
# evidence refreshes every couple of hundred milliseconds.  Deriving
# PTY_UNRESPONSIVE from that would assert something never measured, so
# responsiveness stays UNKNOWN until a defensible out-of-band heartbeat exists.
# Explicit degraded state: the Host cannot inspect its own process tree, so no
# lifecycle claim can be made.  This is reported rather than silently treated
# as healthy or dead.
PROCESS_INSPECTION_UNAVAILABLE = "PROCESS_INSPECTION_UNAVAILABLE"

# Tri-state responsiveness.  UNKNOWN is the honest default: this Host has no
# out-of-band PTY heartbeat, so it must not claim either answer.
PTY_RESPONSIVE = "RESPONSIVE"
PTY_UNRESPONSIVE_OBSERVED = "UNRESPONSIVE"
PTY_RESPONSIVENESS_UNKNOWN = "UNKNOWN"

MANAGED_SHELL_STATES = (
    SHELL_READY,
    CLI_STARTING,
    CLI_ATTACHED,
    CLI_RUNNING,
    SHELL_IDLE,
    CLI_START_FAILED,
    HOOK_TIMEOUT,
    PTY_UNRESPONSIVE,
    SHELL_EXITED,
    PROCESS_INSPECTION_UNAVAILABLE,
)

# States that mean "this terminal is observably alive right now".  Used for
# rendering only; see the module docstring on currentness.
MANAGED_SHELL_LIVE_STATES = frozenset(
    {SHELL_READY, CLI_STARTING, CLI_ATTACHED, CLI_RUNNING, SHELL_IDLE}
)

DEFAULT_HOOK_TIMEOUT_SECONDS = 45.0
DEFAULT_PTY_PROBE_TIMEOUT_SECONDS = 10.0
DEFAULT_INTERRUPT_GRACE_SECONDS = 5.0

# Characters that cannot survive a command line at all.
_FORBIDDEN_ARGUMENT_CHARACTERS = ("\x00", "\r", "\n")
# ``%`` is expanded by cmd even inside double quotes and cannot be escaped in a
# non-batch context, so an argument carrying one is refused rather than
# silently rewritten into something else.
_CMD_EXPANSION_CHARACTER = "%"
# ``!`` is expanded when delayed expansion is enabled in the child.
_CMD_DELAYED_EXPANSION_CHARACTER = "!"
_NEEDS_QUOTING = re.compile(r'[\s"^&|<>()]')


class ManagedShellError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProcessIdentity:
    """A PID paired with its start time.

    The pair is the identity.  A PID on its own is reused by the OS and would
    let an unrelated process inherit a terminal's lifecycle.
    """

    pid: int
    started_at: float

    def matches(self, other: "ProcessIdentity | None", *, tolerance: float = 0.5) -> bool:
        if other is None:
            return False
        return self.pid == other.pid and abs(self.started_at - other.started_at) <= tolerance

    def as_dict(self) -> dict[str, Any]:
        return {"pid": self.pid, "started_at": self.started_at}


@dataclass(frozen=True)
class AttachEvidence:
    """Runtime-owned evidence that a SessionStart hook ran inside our shell.

    The hook observes the *parent* cmd process it was launched under and its own
    CLI process, so the Supervisor can seal exactly which child owns this
    terminal instead of trusting whichever child happens to be listed first.
    """

    terminal_id: str
    shell: ProcessIdentity
    cli: ProcessIdentity | None = None
    provider: str = ""
    provider_session_ref: str = ""
    session_anchor_ref: str = ""
    observed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "universe.managed-shell-attach-evidence.v1",
            "terminal_id": self.terminal_id,
            "shell_pid": self.shell.pid,
            "shell_started_at": self.shell.started_at,
            "cli_pid": self.cli.pid if self.cli else None,
            "cli_started_at": self.cli.started_at if self.cli else None,
            "provider": self.provider,
            "provider_session_ref": self.provider_session_ref,
            "session_anchor_ref": self.session_anchor_ref,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class ShellObservation:
    """One sample of the owned process tree.

    ``cli_children`` holds only processes the Supervisor itself started under
    the managed shell.  An unrelated process that happens to run under the same
    console is not part of this terminal's lifecycle.  ``inspection_available``
    is False when the Host cannot read its own process tree at all.
    """

    shell_alive: bool
    shell: ProcessIdentity | None = None
    cli_children: tuple[ProcessIdentity, ...] = ()
    # One of PTY_RESPONSIVE / PTY_UNRESPONSIVE_OBSERVED / UNKNOWN.  Nothing
    # currently produces UNRESPONSIVE; see the note on PTY_UNRESPONSIVE.
    pty_responsive: str = PTY_RESPONSIVENESS_UNKNOWN
    inspection_available: bool = True


@dataclass
class ManagedShell:
    """Lifecycle for one Anchor-bound managed cmd shell."""

    terminal_id: str
    session_anchor_ref: str
    provider: str = ""
    shell: ProcessIdentity | None = None
    attach_evidence: AttachEvidence | None = None
    cli_launch_requested_at: float | None = None
    cli_ever_attached: bool = False
    hook_timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS
    failure_evidence: list[dict[str, Any]] = field(default_factory=list)
    last_state: str = SHELL_READY
    # Set when a HOOK_TIMEOUT interrupt has been delivered.  The terminal is
    # only closed on a later sample, once this deadline has passed and the CLI
    # is still there, so recovery never blocks the caller.
    grace_deadline: float | None = None

    def __post_init__(self) -> None:
        # Anchor-before-spawn is a hard precondition, not a nicety: without it
        # the PTY would be the thing that invents the coordinate.
        if not str(self.session_anchor_ref or "").strip():
            raise ManagedShellError(
                "MANAGED_SHELL_ANCHOR_REQUIRED",
                "a Session Anchor must be resolved before the managed shell spawns",
            )

    def bind_shell_identity(self, shell: ProcessIdentity | None) -> None:
        """Record the exact cmd process this terminal owns.

        Without this the shell identity stays None and every attach receipt is
        rejected as a mismatch, which makes the whole lifecycle inert.
        """

        self.shell = shell
        if shell is None:
            self.last_state = PROCESS_INSPECTION_UNAVAILABLE

    def record_cli_launch(self, *, at: float) -> None:
        self.cli_launch_requested_at = float(at)

    def record_attach_evidence(self, evidence: AttachEvidence) -> None:
        """Accept hook evidence only when it names this terminal and shell."""

        if evidence.terminal_id != self.terminal_id:
            raise ManagedShellError(
                "MANAGED_SHELL_ATTACH_TERMINAL_MISMATCH",
                "attach evidence names a different terminal",
            )
        # Anchor-first: a receipt that does not state the Anchor cannot be
        # attributed to this session at all, so it is refused rather than
        # accepted on the strength of matching process identities.
        claimed_anchor = str(evidence.session_anchor_ref or "").strip()
        if not claimed_anchor:
            raise ManagedShellError(
                "MANAGED_SHELL_ATTACH_ANCHOR_REQUIRED",
                "attach evidence must state its Session Anchor",
            )
        if claimed_anchor != str(self.session_anchor_ref or "").strip():
            raise ManagedShellError(
                "MANAGED_SHELL_ATTACH_ANCHOR_MISMATCH",
                "attach evidence names a different Session Anchor",
            )
        if self.shell is None:
            raise ManagedShellError(
                "MANAGED_SHELL_IDENTITY_UNAVAILABLE",
                "managed shell identity was never bound; cannot verify attachment",
            )
        if not evidence.shell.matches(self.shell):
            raise ManagedShellError(
                "MANAGED_SHELL_ATTACH_SHELL_MISMATCH",
                "attach evidence does not match the managed shell process identity",
            )
        self.attach_evidence = evidence
        self.cli_ever_attached = True

    def record_failure_evidence(self, kind: str, detail: Mapping[str, Any]) -> None:
        self.failure_evidence.append(
            {
                "schema": "universe.managed-shell-failure-evidence.v1",
                "terminal_id": self.terminal_id,
                "kind": str(kind),
                "detail": dict(detail),
            }
        )

    def evaluate(self, observation: ShellObservation, *, now: float) -> str:
        """Derive the lifecycle state from the process tree and hook receipt."""

        state = self._evaluate(observation, now=now)
        self.last_state = state
        return state

    def _evaluate(self, observation: ShellObservation, *, now: float) -> str:
        if not observation.inspection_available:
            # Never guess a lifecycle from an unreadable process tree.
            return PROCESS_INSPECTION_UNAVAILABLE
        if not observation.shell_alive:
            return SHELL_EXITED
        if observation.pty_responsive == PTY_UNRESPONSIVE_OBSERVED:
            # Only an explicitly observed non-answer produces this state.  An
            # UNKNOWN signal must never be read as unresponsive.  This is
            # deliberately distinct from SHELL_EXITED: history is still worth
            # preserving and only this terminal should be torn down.
            return PTY_UNRESPONSIVE
        if observation.shell is not None and self.shell is not None:
            if not observation.shell.matches(self.shell):
                # Same PID, different process: the shell we owned is gone.
                return SHELL_EXITED

        if self._attached_child(observation) is not None:
            return CLI_RUNNING
        if self.cli_launch_requested_at is None:
            return SHELL_READY
        if observation.cli_children:
            # A CLI process exists but no matching hook receipt has arrived.
            if self._past_hook_deadline(now):
                return HOOK_TIMEOUT
            return CLI_STARTING
        # No CLI process under our shell.
        if self.cli_ever_attached:
            return SHELL_IDLE
        if self._past_hook_deadline(now):
            return CLI_START_FAILED
        return CLI_STARTING

    def _attached_child(self, observation: ShellObservation) -> ProcessIdentity | None:
        """Return the sealed CLI child, never an arbitrary first child."""

        evidence = self.attach_evidence
        if evidence is None or not observation.cli_children:
            return None
        # The receipt proves a hook ran under our shell; the child list proves
        # the CLI is still there.  Both are required.
        if not evidence.shell.matches(observation.shell or self.shell):
            return None
        if evidence.cli is None:
            # Older receipts carry no CLI identity.  Ownership cannot be sealed,
            # so this is not treated as an attached CLI.
            return None
        for child in observation.cli_children:
            if evidence.cli.matches(child):
                return child
        return None

    def _past_hook_deadline(self, now: float) -> bool:
        if self.cli_launch_requested_at is None:
            return False
        return (now - self.cli_launch_requested_at) >= self.hook_timeout_seconds


@dataclass(frozen=True)
class TimeoutAction:
    step: str
    terminal_id: str
    target_pid: int | None = None
    detail: str = ""


def plan_hook_timeout_recovery(
    shell: ManagedShell,
    observation: ShellObservation,
    *,
    grace_seconds: float = DEFAULT_INTERRUPT_GRACE_SECONDS,
    now: float | None = None,
) -> list[TimeoutAction]:
    """Plan the bounded recovery for one managed HOOK_TIMEOUT.

    Terminal history and failure evidence are preserved first, then only this
    terminal's CLI is interrupted, then after a grace period only this
    terminal's cmd PTY is closed.  Nothing here touches another terminal, the
    Supervisor, or any Anchor.
    """

    actions: list[TimeoutAction] = []
    if shell.grace_deadline is None:
        # First pass only: preserve history, record the failure once, interrupt
        # this terminal's CLI, and open the grace window.  Later in-grace
        # samples must not re-emit evidence for the same timeout.
        actions.append(
            TimeoutAction(
                step="PRESERVE_HISTORY",
                terminal_id=shell.terminal_id,
                detail="retain terminal scrollback and audit trail before recovery",
            )
        )
        actions.append(
            TimeoutAction(
                step="RECORD_FAILURE_EVIDENCE",
                terminal_id=shell.terminal_id,
                detail="record HOOK_TIMEOUT with the observed process tree",
            )
        )
        for child in observation.cli_children:
            actions.append(
                TimeoutAction(
                    step="INTERRUPT_CLI",
                    terminal_id=shell.terminal_id,
                    target_pid=child.pid,
                    detail="interrupt only this terminal's CLI",
                )
            )
        actions.append(
            TimeoutAction(
                step="START_GRACE",
                terminal_id=shell.terminal_id,
                detail=f"give the CLI {grace_seconds:g}s to exit on its own",
            )
        )
        return actions
    if now is not None and now < shell.grace_deadline:
        # Still inside the grace window; wait rather than closing.
        actions.append(
            TimeoutAction(
                step="GRACE",
                terminal_id=shell.terminal_id,
                detail="grace window is still open",
            )
        )
        return actions
    if not observation.cli_children:
        # The CLI honoured the interrupt; the shell survives and stays usable.
        actions.append(
            TimeoutAction(
                step="GRACE_SATISFIED",
                terminal_id=shell.terminal_id,
                detail="CLI exited within the grace window; shell preserved",
            )
        )
        return actions
    actions.append(
        TimeoutAction(
            step="CLOSE_SHELL_PTY",
            terminal_id=shell.terminal_id,
            target_pid=shell.shell.pid if shell.shell else None,
            detail="grace expired with the CLI still running; close only this PTY",
        )
    )
    return actions


def quote_windows_argument(value: str) -> str:
    """Quote one argument for a Windows command line.

    Follows the CommandLineToArgvW backslash/quote rules, and refuses input
    that cannot be represented safely.  ``%`` and ``!`` are refused because cmd
    expands them even inside double quotes, so an argument carrying one cannot
    be passed through literally -- accepting it would silently change the
    command or let a crafted value inject one.
    """

    text = str(value)
    for character in _FORBIDDEN_ARGUMENT_CHARACTERS:
        if character in text:
            raise ManagedShellError(
                "MANAGED_SHELL_ARGUMENT_UNSAFE",
                "argument contains a character that cannot cross a command line",
            )
    if _CMD_EXPANSION_CHARACTER in text or _CMD_DELAYED_EXPANSION_CHARACTER in text:
        raise ManagedShellError(
            "MANAGED_SHELL_ARGUMENT_UNSAFE",
            "argument contains a cmd expansion character that cannot be escaped",
        )
    if '"' in text:
        # cmd /s strips exactly the outer quote pair and treats the remainder
        # literally.  A literal quote inside that remainder cannot be escaped
        # unambiguously for both cmd and CommandLineToArgvW, so this fails
        # closed rather than emitting a command line whose meaning depends on
        # which parser reads it.
        raise ManagedShellError(
            "MANAGED_SHELL_ARGUMENT_UNSAFE",
            "argument contains a literal quote that cannot cross cmd /s safely",
        )
    if text and not _NEEDS_QUOTING.search(text):
        return text
    quoted = ['"']
    backslashes = 0
    for character in text:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"':
            quoted.append("\\" * (backslashes * 2 + 1))
            quoted.append('"')
        else:
            quoted.append("\\" * backslashes)
            quoted.append(character)
        backslashes = 0
    quoted.append("\\" * (backslashes * 2))
    quoted.append('"')
    return "".join(quoted)


def managed_provider_command_line(
    command: Sequence[str], *, pipe_console_input: bool = False
) -> str:
    """Build the exact command written into the persistent managed shell."""

    parts = [str(part) for part in command if str(part).strip()]
    if not parts:
        raise ManagedShellError(
            "MANAGED_SHELL_COMMAND_REQUIRED", "a CLI command is required"
        )
    joined = " ".join(quote_windows_argument(part) for part in parts)
    if pipe_console_input:
        # Claude stream-json refuses a TTY stdin. Windows' built-in MORE is a
        # transparent console-to-pipe bridge inside the one managed cmd.
        joined = f"more | {joined}"
    return joined


def managed_shell_cmdline(
    command: Sequence[str], *, pipe_console_input: bool = False
) -> str:
    """Build the raw cmd.exe argument line that hosts one provider CLI.

    This is the only managed launch builder.  An argv-list variant existed and
    could not work: ``subprocess.list2cmdline`` escapes the pre-quoted ``/s``
    token, so cmd received ``\\"...\\"`` and reported RC 9009 "not
    recognized".  Keeping it alongside this one invited a caller to pick the
    broken path, so it was removed rather than deprecated.

    ``/k`` rather than ``/c`` is load-bearing: the owned shell must outlive a
    normal CLI exit.  With ``/c`` the cmd process dies together with the CLI, so
    "shell alive, CLI gone" never occurs and SHELL_IDLE is unreachable -- every
    normal exit would be indistinguishable from SHELL_EXITED, and
    CLI_START_FAILED could not be told apart from a shell that never ran.

    ``/d`` skips AutoRun so a user profile cannot inject work into a
    Supervisor-owned shell, ``/q`` keeps it headless, and ``/s`` makes cmd strip
    exactly the outer quote pair and treat the remainder literally.

    This exists because ``subprocess.list2cmdline`` cannot express the ``/s``
    form: it escapes any literal quote, so a pre-quoted token arrives at cmd as
    ``\"...\"`` and is read as a program name -- the observed RC 9009 launch
    failure.  The command line therefore has to be produced verbatim and handed
    to the spawn path without a second round of quoting.

    Returns only the arguments after the executable, which is what the ConPTY
    backend appends to the program name.
    """

    joined = managed_provider_command_line(
        command, pipe_console_input=pipe_console_input
    )
    return f'/d /q /s /k "{joined}"'


def observe_process_tree(
    shell: ProcessIdentity | None,
    *,
    is_alive: Callable[[int], bool],
    children_of: Callable[[int], Sequence[ProcessIdentity]],
    start_time_of: Callable[[int], float | None],
    pty_responsive: str = PTY_RESPONSIVENESS_UNKNOWN,
    inspection_available: bool = True,
) -> ShellObservation:
    """Sample the owned process tree through injected Host probes."""

    if not inspection_available:
        return ShellObservation(
            shell_alive=False,
            shell=shell,
            pty_responsive=pty_responsive,
            inspection_available=False,
        )
    if shell is None:
        return ShellObservation(shell_alive=False, pty_responsive=pty_responsive)
    if not is_alive(shell.pid):
        return ShellObservation(shell_alive=False, shell=shell, pty_responsive=pty_responsive)
    started = start_time_of(shell.pid)
    observed = (
        ProcessIdentity(pid=shell.pid, started_at=float(started))
        if started is not None
        else None
    )
    return ShellObservation(
        shell_alive=True,
        shell=observed,
        cli_children=tuple(children_of(shell.pid)),
        pty_responsive=pty_responsive,
    )


def process_inspection_available() -> bool:
    """Report whether this Host can read its own process tree."""

    return host_process_probes() is not None


def _native_host_probes() -> dict[str, Any] | None:
    """Windows-native probes.

    psutil is absent on the service and test interpreters, so native
    inspection is the primary path rather than a nicety.
    """

    try:
        from universe_app.windows_process import native_probes
    except ImportError:
        return None
    probes = native_probes()
    if probes is None:
        return None

    def children_of(pid: int) -> list[ProcessIdentity]:
        found: list[ProcessIdentity] = []
        for child in probes["child_pids"](pid):
            started = probes["start_time_of"](child)
            if started is None:
                # Without a start time the PID is not an identity; skip it
                # rather than pairing it with a placeholder.
                continue
            found.append(ProcessIdentity(pid=int(child), started_at=float(started)))
        return found

    return {
        "is_alive": probes["is_alive"],
        "children_of": children_of,
        "start_time_of": probes["start_time_of"],
        "source": "WINDOWS_NATIVE",
    }


def host_process_probes() -> dict[str, Any] | None:
    """Build real Host process probes, or None when unavailable.

    Returning None is an explicit unavailable signal.  Callers must surface
    PROCESS_INSPECTION_UNAVAILABLE rather than quietly doing nothing.
    """

    native = _native_host_probes()
    if native is not None:
        return native
    try:
        import psutil  # type: ignore
    except ImportError:
        return None

    def is_alive(pid: int) -> bool:
        try:
            return psutil.Process(pid).is_running()
        except Exception:  # noqa: BLE001 - absent process is simply not alive
            return False

    def start_time_of(pid: int) -> float | None:
        try:
            return float(psutil.Process(pid).create_time())
        except Exception:  # noqa: BLE001
            return None

    def children_of(pid: int) -> list[ProcessIdentity]:
        try:
            parent = psutil.Process(pid)
            return [
                ProcessIdentity(pid=child.pid, started_at=float(child.create_time()))
                for child in parent.children(recursive=False)
            ]
        except Exception:  # noqa: BLE001
            return []

    return {
        "is_alive": is_alive,
        "children_of": children_of,
        "start_time_of": start_time_of,
        "source": "PSUTIL",
    }
