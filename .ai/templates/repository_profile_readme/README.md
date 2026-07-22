# Repository Profile README Template

Use this template when a repository README should serve both humans and LLM sessions or agents.

The README is Entry Point 0. It should explain repository identity before deeper boot documents are loaded.

## Target Path

```text
README.md
```

## Template Shape

```md
# <repository-name>

<One or two sentence repository summary.>

## Repository Profile

| Field | Value |
| --- | --- |
| Repository | `<owner>/<repo>` |
| Type | `<LLM collaboration runtime | target project | library | service | tool>` |
| Audience | `Human + LLM sessions or agents` |
| Purpose | `<main purpose>` |
| Primary workspace | `.ai/` if present |

## What This Is

<Explain what this repository is and what it is not.>

## Audience

Human readers should know where to start.

LLM sessions and agents should treat this README as Repository Profile / Entry Point 0 and then continue to the appropriate boot documents.

Suggested runtime entry order:

1. `AGENTS.md`
2. `.ai/README.md`
3. `.ai/START_HERE.md` or role-specific resume manifest when present

Reading the repository is not permission to modify it. Changes require explicit scoped authority.

## Operating Model

<Describe the repository's operating model.>

For `ai-career`, include Top-Down and Bottom-Up flows.

For a target project, include project Master / Worker / report flow.

## Core Roles

| Role | Responsibility |
| --- | --- |
| User | <final approval / product direction> |
| Conductor | <governance if applicable> |
| Project Master | <project orchestration if applicable> |
| Workers | <scoped execution> |
| Carrier | <candidate collection if applicable> |

## Core Concepts

### Runtime Workspace

<Define `.ai/` if present.>

### Resume

<Define restore state if present.>

### Candidate PR

<Define project-to-governance boundary if applicable.>

## Entry Points

| Path | Purpose |
| --- | --- |
| `README.md` | Human + LLM repository profile |
| `AGENTS.md` | Local agent operating rules |
| `.ai/README.md` | Runtime workspace layout |
| `.ai/START_HERE.md` | Runtime boot manager entry, if present |

## Safety Rules

- Do not store secrets.
- Do not store private chain-of-thought.
- Do not treat boot/readiness as file modification approval.
- Keep project-specific facts separate from reusable governance rules.
```

## Rules

1. Keep README concise.
2. Put detailed policy in `.ai/` or `docs/`.
3. Make the repository type explicit.
4. Make the human and LLM runtime entry paths explicit.
5. Treat README as profile, not as the full operating manual.
