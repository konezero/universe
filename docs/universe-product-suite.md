# Universe Product Suite

Status: proposed product-boundary contract

## Product topology

`Universe`, `Career`, and `Rendezvous` are independently deployable products
in one product suite. They may use separate repositories and release cadences,
but they must not present competing project-installation or authority models.

```text
                         Universe Product Suite

    +------------------+    +------------------+    +------------------+
    | Universe         |    | Career           |    | Rendezvous       |
    | application      |    | runtime product  |    | network product  |
    +--------+---------+    +--------+---------+    +--------+---------+
             |                       |                       |
             | project lifecycle     | immutable releases    | remote discovery
             +-----------------------+-----------------------+
                                     |
                                     v
                              attached Projects
```

## Product responsibilities

| Product | Owns | Does not own |
|---|---|---|
| **Universe** | User application, project registry, Project/Node graph, Todo, session observatory, Provider routing, project attachment, local service/tray, project-installation orchestration, project-integration templates | Project source execution authority, raw provider transcripts, external discovery infrastructure |
| **Career** | Shared Runtime, governance contracts, Skills, release builder, immutable Release DB payloads, lifecycle validation contracts | User-facing project portfolio, project attachment policy, remote browser pairing, Universe application state |
| **Rendezvous** | Universe UUID registration, visibility/discovery metadata, owner-approved connection requests, presence, route resolution, Relay/tunnel adapters | Local project files, Provider credentials, Career Runtime payloads, execution authority |
| **Attached Project** | Product source, product documentation, its own product board, local installed Runtime instance | Suite-wide release building, Universe registry, remote access infrastructure |

## Ownership rules

1. **Universe is the sole project-entry product.** New or existing projects
   attach through Universe. A project may still run standalone, but that is a
   Universe-defined installation mode, not a second Career entry path.
2. **Career is the sole Runtime producer.** It builds verified immutable
   Release DB artifacts. It does not distribute a project attachment UX or
   require consumer projects to read Career Git.
3. **Rendezvous is the sole remote-discovery product.** It can resolve and
   route a Universe connection, but never grants Career authority, Provider
   permission, or Project execution permission.
4. **A project owns only a small tracked binding.** The tracked contract is
   `.universe/project.json`; the installed `.ai/` workspace is local state and
   must be ignored by the project repository.
5. **No product substitutes another product's evidence.** A Rendezvous browser
   session is not an execution approval. A Career release is not an attached
   project. A Universe Project connection is not a Provider session.

## Template ownership

Project-integration templates belong to Universe. Runtime and governance
templates belong to Career.

| Template family | Owner after migration | Consumer |
|---|---|---|
| `.universe/project.json`, project attach/standalone bootstrap, local `.ai` ignore rule, project root entry guidance | Universe | attached Project |
| Project Todo policy, Node Memory placement, Seed/document projection integration | Universe | attached Project |
| Runtime package, Mode/Skill contracts, Execution Guard, lifecycle validator, release manifest | Career | Universe installer and local Project Runtime |
| UUID manifest, discovery visibility, pairing request, route/relay configuration | Rendezvous | Universe application and remote browser |

Career may provide Runtime payload templates needed to materialize a local
`.ai/` instance. It must not remain the source of project-attachment policy or
the project-facing Universe seed template after this migration.

## Installation and update flow

```text
Career source
  -> immutable Career Release DB
  -> Universe release catalog
  -> Universe Project Lifecycle Host
  -> attached Project local .ai/ instance

Universe project template
  -> tracked .universe/project.json + root entry guidance
  -> attached Project repository

Rendezvous registration
  -> owner-approved route resolution
  -> remote browser to Universe application
```

The remote route only reaches the Universe application. All Project updates
continue through the local Universe lifecycle path and the installed Career
Runtime contracts.

## Migration rules

1. Create a Universe-owned project-template catalog without changing Career
   Runtime payload ownership.
2. Publish the Universe project-template catalog as the canonical source for
   project binding, Todo, connection, Node Memory, and Seed/attach guidance.
3. Leave compatibility references in Career only until a Universe release can
   install the new catalog without direct Career Git access.
4. Migrate each consumer project non-destructively: add
   `.universe/project.json`, ignore `.ai/`, then remove `.ai/` from Git index
   while preserving its local files.
5. Keep `ai-career`, `universe`, and `universe-rendezvous` source workspaces
   tracked according to their own product ownership. Only consumer projects
   receive the local-only `.ai/` rule.

## Acceptance criteria

- A new project can attach or run standalone without cloning Career.
- Universe can update a Project local Runtime from a verified Career Release
  DB without making `.ai/` a tracked Project tree.
- Project-integration templates are versioned and released by Universe.
- Universe can present a digest-bound, non-executing integration proposal that
  separates the tracked `.universe/` binding from local `.ai/` assets before
  either installation path starts.
- Career can build and validate its Runtime Release DB without importing
  Universe application code.
- Rendezvous can resolve a Universe route without receiving a local API token,
  Provider credential, or Project Runtime secret.
