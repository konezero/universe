# universe Runtime Scope Policy

Schema: ai-career.project-runtime-scope.v1
Managed Inventory: `.ai/runtime/project_instance/DISTRIBUTION_MANIFEST.json`
Runtime Core Index: `.ai/core/README.md`

The installer may update only manifest-owned paths. Unmanaged existing
files require explicit force. Excluded source roots are never copied
or managed; existing project-owned data under those roots is preserved.

Project mutation requires the active Session Boot process, deterministic
Execution Guard, target-matched Write Scope and Assignment, approval,
and a receipt-aware Host path. Generic filesystem access is capability,
not Write Scope or execution permission.

Authority: UNASSIGNED
Execution Assignment: UNASSIGNED
