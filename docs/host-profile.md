# Host Profile

The Host Profile is the local source of truth for external executables used by
Universe and installed project Runtime adapters.

## Location

The Profile path is:

```text
AI_CAREER_HOST_PROFILE
```

When the variable is absent, Windows uses:

```text
%LOCALAPPDATA%\ai-career\host.json
```

The file is local machine state. It is not part of a project, a Career Release
DB, or source control.

## Managed Tools

Version 1 manages:

```text
python
git
codex
grok
```

Python and Git are required for a ready base Profile. Codex and Grok are
optional provider capabilities. Each record contains:

```text
status
executable
version
verified_at
discovery_source
environment
evidence_ref
reason
```

Only `GROK_HOME` is permitted in the stored launch environment. Secrets,
tokens, cookies, provider sessions, and arbitrary environment variables are
invalid Profile content.

## Initialization And Migration

Universe initializes the Profile before local Runtime hosts start. Discovery
uses this order:

1. `AI_CAREER_<TOOL>_EXECUTABLE`
2. legacy migration input where applicable
3. current native Python or a known native application location
4. `PATH`

Legacy `CODEX_CLI_PATH` and `GROK_HOME` are read only while discovering a
Profile entry. Runtime callers do not read them after initialization.

Every candidate must be a native executable and pass `--version` through the
Windows native CLI runner. `.bat`, `.cmd`, and `.ps1` launchers are rejected.
A stale or inaccessible Profile path resolves to `UNAVAILABLE`; callers do not
silently fall back to another discovery route.

## Runtime Settings

The local UI and API can:

- inspect the active Profile and its path;
- rediscover all tools;
- select one exact executable path;
- reverify one stored executable.

Selecting or verifying a Host tool changes local application configuration
only. It grants no governance authority, execution assignment, write scope, or
source mutation permission.
