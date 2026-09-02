from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_execution_guard_defines_governed_knowledge_route() -> None:
    guard = _read(".ai/skills/common/execution-guard/SKILL.md")

    assert "### GOVERNED_KNOWLEDGE_WRITE" in guard
    assert "named Action Gateway rather than raw SQL" in guard
    assert "does not mint an Execution Guard receipt" in guard
    assert "conflicting content is rejected" in guard
    assert "do not call `execution-guard check`" in guard


def test_direct_user_write_routes_governed_knowledge_separately() -> None:
    direct_write = _read(".ai/skills/common/direct-user-write/SKILL.md")

    assert "4. `GOVERNED_KNOWLEDGE_WRITE`" in direct_write
    assert "Canonical RAG, decision memory" in direct_write
    assert "Do not call Execution Guard for the knowledge record itself" in direct_write
    assert "non-governed write-capable API effect" in direct_write


def test_core_execution_contract_excludes_validated_knowledge_actions() -> None:
    pre_execution = _read(".ai/core/PRE_EXECUTION_VERIFICATION.md")
    authority_binding = _read(
        ".ai/core/RUNTIME_AUTHORITY_EXECUTION_BINDING.md"
    )

    assert "## Governed Knowledge Action Boundary" in pre_execution
    assert "named Action Gateway enforces the knowledge boundary" in pre_execution
    assert "Knowledge Action Readiness" in pre_execution
    assert "Guarded execution requires a scoped execution assignment" in authority_binding
    assert "not reclassified as an unclassified durable artifact" in " ".join(
        authority_binding.split()
    )


def test_generated_policy_references_use_the_narrow_guard_scope() -> None:
    generated_policy_sources = (
        "AGENTS.md",
        ".ai/START_HERE.md",
        "REPOSITORY_MANIFEST.md",
        ".ai/distribution/context_management_runtime_pack/project_runtime_installer.py",
    )

    for relative_path in generated_policy_sources:
        policy = _read(relative_path)
        assert "governed knowledge" in policy.lower()
        assert (
            "non-governed write-capable API" in policy
            or "validated Action Gateway" in policy
            or "named Action Gateway" in policy
        )

    assert (
        "Before every project-owned file create/edit/delete/move, write-capable API"
        not in _read("AGENTS.md")
    )
    assert "Before every **project-owned** durable mutation" not in _read(
        ".ai/START_HERE.md"
    )
