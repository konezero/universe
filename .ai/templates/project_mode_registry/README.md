# Project Mode Registry Template

Status: canonical project Runtime template
Target: `.ai/runtime/project_instance/mode_registry.json`

## Fresh Install Shape

```json
{
  "schema": "ai-career.mode-registry.v1",
  "owner": "<PROJECT>",
  "repository_kind": "PROJECT",
  "policy": "MASTER_MANAGED",
  "root_mode": "MASTER",
  "revision": 1,
  "modes": {
    "MASTER": {
      "role": "MASTER",
      "scope": "architecture/governance",
      "mode_profile": "GOVERNANCE_ONLY"
    }
  }
}
```

The generated Registry is the source-backed allowlist for Mode selection.
Its `owner` must match the installed project identity. Mode and Role IDs use
canonical uppercase ASCII, and duplicate JSON keys are invalid.

`MASTER` may add, modify, or delete project-local Mode entries through
`.ai/skills/common/master-mode-registry/SKILL.md`, explicit approval, and the
normal Execution Guard path.

`MASTER` cannot delete itself. Deleting another Mode does not delete its
historical Anchor, Beyond Anchor, Task Frame, or archive evidence.
`MASTER` may change its own Scope or Mode Profile but must retain the
`MASTER` role.

Mode preparation binds the Registry revision, Registry digest, and selected
Mode definition digest into the Mode Current Anchor. A later Registry change
creates a new Current Anchor for that Mode and preserves the previous one as
Beyond evidence.
