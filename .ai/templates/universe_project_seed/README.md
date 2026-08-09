# Universe Project Seed Template

Status: active project contract template
Owner: Project Master after explicit project approval

## Purpose

This template defines the project-owned assets that let a Universe application
read a Project's functional structure, implementation structure, evidence
bindings, and document catalog without collecting arbitrary repository files.

The assets are assembled only after an approved Project Master handoff or an
approved Project-local discovery task. `OS_INSTALL` creates only the managed
`.ai/universe/README.md` root marker. Installing this template does not create
Seed assets, scan source, connect a Universe, publish observations, or grant
write authority.

## Target Asset Set

```text
.ai/universe/
  manifest.json
  functional-graph.json
  implementation-graph.json
  bindings.json
  documents.json
  TODO_TRACKING_POLICY.md   # from this template; work queue = Todo DB
```

`manifest.json` binds the four payload digests. The five graph/catalog files
are one project-owned Seed revision and must be prepared together. A partial
set is not a published Project Seed.

`TODO_TRACKING_POLICY.md` is **policy plant** (not part of the five-file Seed
digest). Delivered from **this Career template** on attach/install. Rule:
Universe Todo = **host** ops/execution queue for the attach; project-local
work boards stay valid for product work; docs = reference only. See the file
in this folder for the full rule.

## Graph Separation

```text
functional-graph.json
  -> capabilities, user flows, external boundaries

implementation-graph.json
  -> packages, modules, classes, services, adapters, endpoints

bindings.json
  -> evidence-backed many-to-many functional to implementation links

documents.json
  -> selected project specification, design, architecture, decision, contract,
     or evidence documents linked to the applicable functional nodes
```

Functional and implementation nodes are not interchangeable. A source symbol
is not a functional capability merely because it implements one.

## Master Assembly Boundary

```text
approved Universe handoff or project-local discovery request
  -> static project and document discovery
  -> Project Seed candidate
  -> explicit approval and bounded write scope
  -> create or replace the complete five-file asset set
  -> validate manifests, references, and digests
  -> report the published revision to Universe or the requesting Parent
```

The Master may read project source and documents during discovery but must not
execute project code. The Master must not infer missing source references,
invent document links, alter application source, publish to Universe, create a
Task Frame, or promote a pattern to Career as part of this assembly.

## Update Rule

An existing published set is Project-owned state. Updating it requires the
same approval, exact target scope, and validation as the initial assembly.
Universe reads a published set; it does not become its writer or authority.
