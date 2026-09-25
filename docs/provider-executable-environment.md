# Provider executable selection and child environments

The saved Host Profile is the launch authority. Environment variables are
child-process transport, not independently editable copies of the selection.

| Variable | Owner / behavior |
| --- | --- |
| AI_CAREER_CODEX_EXECUTABLE | Derived from selected Codex executable at launch |
| CODEX_CLI_PATH | Existing legacy alias; derived from the same Codex executable |
| AI_CAREER_CLAUDE_EXECUTABLE | Derived from selected Claude executable at launch |
| AI_CAREER_GROK_EXECUTABLE | Derived from selected Grok executable at launch |
| UNIVERSE_CODEX_QUEUE_EXECUTABLE | Terminal Host pins this to that session's executable; Rust Host consumes it |
| GROK_HOME | Provider data/session home, not an interchangeable executable alias |
| AI_CAREER_HOST_PROFILE | Selects the Host Profile file, not the executable |

`provider_launch_environment` is shared by Host Profile resolution (Workers)
and interactive Terminal Host creation (including fresh-process resume). These
child overrides do not write Windows user/system environment or mutate the
server's environment. Terminal-specific executable variables are reserved from
caller-provided environment overrides. Authentication and unrelated environment
variables retain their existing handling.

Discovery may use environment/PATH for automatic initial selection. Once a tool
is USER_SELECTED, discovery verifies that exact path without replacing it. A
missing/invalid selected file remains visible as UNAVAILABLE with its selected
path preserved; another environment/PATH executable is not substituted.

CODEX_CLI_PATH is currently retained only as the existing legacy discovery input
and a synchronized child alias, never a second user setting. Repository runtime
code has no other direct consumer; an old diagnostic script under runtime/tmp
does read it and is not a supported launch path. Removing this compatibility
input is a separate migration of automatic initial discovery and its test.

Existing processes retain their launch snapshot. Updating configuration neither
upgrades a running process nor changes the executable used by its native queue.
New-process resume uses current configuration; reconnection to an existing Host
keeps its existing process. Web service restart alone does not replace the
persistent PTY supervisor, so deployment must distinguish those process owners.
