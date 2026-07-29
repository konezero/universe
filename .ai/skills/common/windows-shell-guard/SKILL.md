---
name: windows-shell-guard
description: Use before repository search, inspection, build, test, Git, filesystem, process, adapter, Task Frame, or Worker commands on a Windows Host. Reject Bash-only syntax and unexpanded native path globs, validate destructive paths, and route external executables through windows-native-cli.
---

# Windows Shell Guard

Construct commands for the observed Windows shell before execution. Do not emit
a Bash-looking command and wait for it to fail.

## Boundary

- Keep PowerShell cmdlets inside PowerShell.
- Hand `rg`, Git, Python, test tools, provider CLIs, and other external
  executables to `windows-native-cli`.
- Treat syntax and argv transport as Host properties, not authority or sandbox
  evidence.

If the Host OS or active shell is not evidenced, keep the route `UNKNOWN`.

## Workflow

1. Establish `host_os` and `active_shell` from Host evidence.
2. Reject Bash-only syntax on Windows PowerShell.
3. Reject wildcard path operands that depend on shell expansion. Use the
   native CLI's own selector, such as `rg -g '*.py' <root>`.
4. Select a PowerShell, `rg`, Git, or Python form.
5. Validate every destructive filesystem target before recursive delete or
   move.
6. Route external executables through `windows-native-cli`.
7. Execute once and diagnose the returned result.

Use `scripts/check_windows_command.py` for deterministic preflight. It does not
execute the command and is not a complete parser or security sandbox.

## Preferred Forms

| Intent | Preferred form |
| --- | --- |
| Search contents | `rg PATTERN PATH` |
| Search selected files | `rg PATTERN -g '*.py' PATH` |
| List matching files | `rg --files -g '*.py'` |
| Search tracked files | `git grep PATTERN` |
| Read a literal file | `Get-Content -LiteralPath PATH -Raw` |
| First or last records | `Select-Object -First N` / `Select-Object -Last N` |
| Structured processing | Python with explicit input and output files |
| External CLI | Structured argv through `windows-native-cli` |

Do not pass a path such as `.ai/runtime/*.py` to `rg` on Windows. PowerShell
does not expand that path for the native process. Use:

```text
rg PATTERN -g '*.py' .ai/runtime
```

## Reject on PowerShell

Unless Host evidence explicitly identifies Bash or WSL, reject:

- `grep`, `find ... -name`, `cat`, `sed`, `awk`, `head`, and `tail`;
- `rm -rf`, `cp -r`, `mv`, `export`, `chmod`, and Bash line continuation;
- `/dev/null`, process substitution, and POSIX environment prefixes;
- version-dependent `&&` and `||` chains;
- `Invoke-Expression`;
- `Start-Process -ArgumentList` as exact native argv transport;
- wildcard path operands that rely on PowerShell to expand for a native CLI.

For mutation, use `-LiteralPath`. Before recursive delete or move, resolve each
target and verify it stays below the intended workspace or explicitly named
root. Keep enumeration and mutation in one PowerShell process.

## Preflight Request

```json
{
  "schema": "windows-shell-guard.request.v1",
  "host_os": "WINDOWS",
  "active_shell": "POWERSHELL",
  "command": "rg --files -g '*.py'"
}
```

For recursive delete or move, include:

```json
{
  "path_scope": {
    "allowed_roots": ["C:\\workspace\\project"],
    "resolved_targets": ["C:\\workspace\\project\\.tmp"]
  }
}
```

Run:

```powershell
python .ai/skills/common/windows-shell-guard/scripts/check_windows_command.py `
  --request C:\Temp\shell-guard-request.json `
  --result C:\Temp\shell-guard-result.json
```

`WINDOWS_NATIVE_CLI` means apply `windows-native-cli`. `POWERSHELL_INTERNAL`
means the command remains a cmdlet or PowerShell language operation.

## Included Script

- `scripts/check_windows_command.py`: non-executing command preflight
