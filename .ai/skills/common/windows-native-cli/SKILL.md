---
name: windows-native-cli
description: Use whenever a Windows Host invokes an external CLI directly or through a Task Frame, Worker, adapter, service, or test harness. Preserve exact argument boundaries, stdin, environment overrides, timeouts, and machine-readable output without shell-string reconstruction.
---

# Windows Native CLI

Use this Skill after `windows-shell-guard` classifies an operation as
`WINDOWS_NATIVE_CLI`.

## Boundary

Keep the executable, each argument, stdin, environment, and output as separate
transport fields. Do not turn them into one shell command string.

This applies to:

- direct Host CLI calls;
- Task Frame, Boss, Worker, and provider adapters;
- foreground, background, scheduled, and service-owned processes;
- build, test, Git, and repository tool calls.

PowerShell cmdlets that remain inside PowerShell are not external CLI calls. A
native executable launched from PowerShell is.

If Host operating-system evidence is unavailable, keep the route `UNKNOWN`.

## Invariants

1. Prefer the CLI's native input-file, response-file, or stdin option.
2. Otherwise pass an explicit argument array through Python `subprocess` with
   `shell=False`.
3. Do not use `Invoke-Expression`, `cmd /c`, `powershell -Command`, or
   `shell=True` merely to launch a native executable.
4. Do not use `Start-Process -ArgumentList` when exact argument boundaries
   matter.
5. Treat `.bat` and `.cmd` as shell boundaries. The included runner rejects
   them.
6. Keep secrets out of arguments, request JSON, stdout, and stderr.
7. Capture stdout and stderr separately and bound retained output.
8. A timeout does not prove descendant termination.
9. Correct argv transport does not make untrusted code safe. Candidate code
   still requires the source-review execution boundary.
10. When the executable is an agent provider acting subordinate to the active
    Parent, invoke it only through an accepted Task Frame Worker plan. A direct
    provider CLI call from the repository working directory must not substitute
    for Task Frame setup, bounded context, or Host Worker evidence.

## Request

Create a UTF-8 `windows-native-cli.request.v1` document:

```json
{
  "schema": "windows-native-cli.request.v1",
  "executable": "C:\\absolute\\path\\tool.exe",
  "allow_path_lookup": false,
  "args": ["--input-file", "C:\\Temp\\request.json"],
  "cwd": "C:\\workspace\\project",
  "timeout_seconds": 120,
  "output_encoding": "utf-8",
  "max_output_chars": 200000,
  "stdin": {"kind": "NONE"},
  "environment": {
    "inherit": true,
    "set": {},
    "remove": []
  }
}
```

Each `args` element is one exact argument. Preserve an empty argument as `""`.
For file-backed stdin, use:

```json
{"kind": "FILE", "path": "C:\\Temp\\stdin.txt"}
```

Run:

```powershell
python .ai/skills/common/windows-native-cli/scripts/run_native_cli.py `
  --request C:\Temp\native-cli-request.json `
  --result C:\Temp\native-cli-result.json
```

The result status is one of:

```text
COMPLETED
FAILED
TIMED_OUT
LAUNCH_FAILED
REQUEST_INVALID
```

When transport behavior is uncertain, invoke `argv_probe.py` through the
runner with paths containing spaces, empty arguments, quotes, JSON, multiline
text, and non-ASCII text before a billable or write-capable provider call.

## Included Scripts

- `scripts/run_native_cli.py`: structured native process runner
- `scripts/argv_probe.py`: exact argv and stdin observation helper
