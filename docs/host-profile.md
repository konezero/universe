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

Revision 2 manages:

```text
python
git
ssh
codex
grok
claude
```

Python and Git are required for a ready base Profile. OpenSSH is an optional
transport capability. Codex, Grok, and Claude are optional provider
capabilities. Each record contains:

```text
status
executable
version
verified_at
discovery_source
environment
evidence_ref
reason
model
```

`model` is `NOT_APPLICABLE` for Python and Git. Provider tools use one local,
explicit selection. Defaults are `default` for Codex and Claude and
`grok-build` for Grok. Revision 1 Profiles are migrated in place without
rediscovering or replacing an already selected executable.

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

On Windows, Claude discovery includes the official native installer location
`%USERPROFILE%\.local\bin\claude.exe`. Batch and command shims are not used.

Every candidate must be a native executable and pass `--version` through the
Windows native CLI runner. `.bat`, `.cmd`, and `.ps1` launchers are rejected.
A stale or inaccessible Profile path resolves to `UNAVAILABLE`; callers do not
silently fall back to another discovery route.

## Runtime Settings

The local UI and API can:

- inspect the active Profile and its path;
- rediscover all tools;
- select one exact executable path;
- select one exact model for Codex, Grok, or Claude;
- reverify one stored executable.

The selected model is passed to the provider CLI for every new persistent
Mode Session and every ephemeral Task Frame Worker. The same value is bound to
the Task Frame plan and Result Packet `model_ref`; a mismatch stops invocation.
Changing a model closes resident provider sessions so the next message starts
with the new selection. Existing provider-owned session history is not
relabelled as having used the new model.

Selecting or verifying a Host tool changes local application configuration
only. It grants no governance authority, execution assignment, write scope, or
source mutation permission.
