# Label Profile Template

Use this template to define shared operating labels for a repository or project runtime workspace.

Operating labels are used by humans and runtime roles to classify PRs, issues, memos, reports, checkpoints, queues, sessions, and automation runs.

## Target Paths

Recommended shared profile path:

```text
.ai/labels/LABEL_PROFILE.md
```

Optional GitHub setup note:

```text
.ai/labels/GITHUB_LABELS.md
```

## Template Shape

```md
# <Repository or Project> Label Profile

Repository: `<owner>/<repo>`
Project ID: `<project_id>`
Status: candidate
Source profile: `ai-career/docs/OPERATING_LABEL_PROFILE.md`

## Purpose

<Explain how this repository uses labels.>

## Required Axes

Use these axes unless there is a project-specific reason to narrow them:

```text
kind:<object-kind>
role:<responsible-role>
flow:<flow-direction>
source:<origin>
target:<destination>
stage:<processing-stage>
decision:<decision-owner>
carry:<resume-carrier-action>
risk:<risk-level>
```

## Project Source Labels

```text
source:<project_id>
source:human
source:scheduler
source:worker
source:carrier
```

## Project Role Labels

```text
role:worker
role:project-master
role:carrier
role:conductor
role:user
```

## GitHub PR Minimum Labels

Every Candidate PR should include:

```text
kind:pr
flow:bottom-up
source:<project_id>
stage:candidate
decision:conductor
carry:review
```

Add one target label:

```text
target:memo
target:policy
target:project-profile
target:core
target:resume
target:template
target:docs
target:none
```

## Examples

### Project Candidate PR

```text
kind:pr
role:project-master
flow:bottom-up
source:<project_id>
target:policy
stage:candidate
decision:conductor
carry:review
risk:low
```

### Worker Report

```text
kind:report
role:worker
flow:bottom-up
source:<project_id>
target:none
stage:candidate
decision:project-master
carry:review
risk:low
```

### Carrier Queue Item

```text
kind:queue-item
role:carrier
flow:bottom-up
source:<project_id>
target:policy
stage:queued
decision:conductor
carry:review
```

## Session Labels

Recommended format:

```text
<Role> | <Repository or Project>
```

Examples:

```text
Master | <Project>
Review | <Project>
Carrier | ai-career
Conductor | ai-career
```

## Rules

1. Labels do not grant authority.
2. Labels do not approve merge.
3. Labels are metadata for routing and review.
4. Use the smallest useful set of labels.
5. Project-specific labels must not conflict with the shared axes.
```

## Required Rules

1. Keep labels stable and parseable.
2. Prefer axis labels over free-form labels.
3. Use `carry:checkpoint` only for meaningful operating-state changes.
4. Use `decision:user` when merge/adoption needs explicit User approval.
5. Use `decision:conductor` when `ai-career` governance review is required.
