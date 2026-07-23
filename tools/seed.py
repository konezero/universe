from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "seed" / "official-development-seed-v0.json"
DEFAULT_DATABASE = ROOT / "dist" / "official-development-seed-v0.sqlite"
DEFAULT_MANIFEST = ROOT / "dist" / "official-development-seed-v0.manifest.json"
SOURCE_SCHEMA = "universe.official-development-seed-source.v0"
RESULT_SCHEMA = "universe.future-path-candidates.v0"
TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9+.#_-]*", re.IGNORECASE)


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE release_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE project_kind (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE project_kind_keyword (
    project_kind_id TEXT NOT NULL REFERENCES project_kind(id),
    keyword TEXT NOT NULL,
    PRIMARY KEY (project_kind_id, keyword)
);

CREATE TABLE technology (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    category TEXT NOT NULL
);

CREATE TABLE technology_alias (
    technology_id TEXT NOT NULL REFERENCES technology(id),
    alias TEXT NOT NULL,
    PRIMARY KEY (technology_id, alias)
);

CREATE TABLE failure_pattern (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    response TEXT NOT NULL
);

CREATE TABLE failure_signal (
    failure_id TEXT NOT NULL REFERENCES failure_pattern(id),
    signal TEXT NOT NULL,
    PRIMARY KEY (failure_id, signal)
);

CREATE TABLE route (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    support_level TEXT NOT NULL CHECK (support_level = 'CURATED_HYPOTHESIS')
);

CREATE TABLE route_project_kind (
    route_id TEXT NOT NULL REFERENCES route(id),
    project_kind_id TEXT NOT NULL REFERENCES project_kind(id),
    PRIMARY KEY (route_id, project_kind_id)
);

CREATE TABLE route_technology_signal (
    route_id TEXT NOT NULL REFERENCES route(id),
    technology_id TEXT NOT NULL REFERENCES technology(id),
    PRIMARY KEY (route_id, technology_id)
);

CREATE TABLE route_goal_signal (
    route_id TEXT NOT NULL REFERENCES route(id),
    signal TEXT NOT NULL,
    PRIMARY KEY (route_id, signal)
);

CREATE TABLE route_failure_pattern (
    route_id TEXT NOT NULL REFERENCES route(id),
    failure_id TEXT NOT NULL REFERENCES failure_pattern(id),
    PRIMARY KEY (route_id, failure_id)
);

CREATE TABLE route_step (
    route_id TEXT NOT NULL REFERENCES route(id),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    purpose TEXT NOT NULL,
    exit_evidence TEXT NOT NULL,
    PRIMARY KEY (route_id, ordinal),
    UNIQUE (route_id, id)
);

CREATE TABLE pivot_rule (
    id TEXT PRIMARY KEY,
    trigger_text TEXT NOT NULL,
    procedure_text TEXT NOT NULL,
    invariant_text TEXT NOT NULL
);
"""


class SeedError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_source(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_source(data)
    return data


def validate_source(data: Any) -> None:
    if not isinstance(data, dict) or data.get("schema") != SOURCE_SCHEMA:
        raise SeedError("unsupported or missing source schema")
    required = {
        "schema",
        "seed_id",
        "version",
        "status",
        "domain",
        "claims",
        "project_kinds",
        "technologies",
        "failure_patterns",
        "routes",
        "pivot_rules",
    }
    if set(data) != required:
        raise SeedError("source has an invalid top-level shape")

    claims = data["claims"]
    expected_claims = {
        "evidence_level": "CURATED_HYPOTHESIS",
        "forecast_output": "CANDIDATE_ONLY",
        "probabilities": "NOT_AVAILABLE",
        "current_anchor_effect": "NONE",
        "authority_effect": "NONE",
        "execution_effect": "NONE",
    }
    if claims != expected_claims:
        raise SeedError("source claims exceed the v0 candidate-only boundary")

    kinds = _unique_ids(data["project_kinds"], "project_kinds")
    technologies = _unique_ids(data["technologies"], "technologies")
    failures = _unique_ids(data["failure_patterns"], "failure_patterns")
    _unique_ids(data["pivot_rules"], "pivot_rules")
    route_ids = _unique_ids(data["routes"], "routes")
    if not route_ids:
        raise SeedError("at least one route is required")

    for route in data["routes"]:
        if not route.get("steps"):
            raise SeedError(f"route {route['id']} has no steps")
        unknown_kinds = set(route.get("project_kinds", [])) - kinds
        unknown_tech = set(route.get("technology_signals", [])) - technologies
        unknown_failures = set(route.get("failure_refs", [])) - failures
        if unknown_kinds or unknown_tech or unknown_failures:
            raise SeedError(f"route {route['id']} contains unknown references")
        step_ids = [step.get("id") for step in route["steps"]]
        if len(step_ids) != len(set(step_ids)) or None in step_ids:
            raise SeedError(f"route {route['id']} has invalid step ids")


def _unique_ids(items: Any, label: str) -> set[str]:
    if not isinstance(items, list):
        raise SeedError(f"{label} must be an array")
    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            raise SeedError(f"{label} contains an invalid id")
        value = item.get("id")
        if not isinstance(value, str) or not value:
            raise SeedError(f"{label} contains an invalid id")
        ids.append(value)
    if len(ids) != len(set(ids)):
        raise SeedError(f"{label} contains duplicate ids")
    return set(ids)


def build_seed(source_path: Path, database_path: Path, manifest_path: Path) -> dict[str, Any]:
    source = load_source(source_path)
    source_bytes = canonical_bytes(source)
    source_sha = sha256_bytes(source_bytes)

    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()

    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(SCHEMA_SQL)
        with connection:
            metadata = {
                "schema": SOURCE_SCHEMA,
                "seed_id": source["seed_id"],
                "version": source["version"],
                "status": source["status"],
                "domain": source["domain"],
                "source_sha256": source_sha,
                **{f"claim.{key}": value for key, value in source["claims"].items()},
            }
            connection.executemany(
                "INSERT INTO release_metadata(key, value) VALUES (?, ?)",
                sorted(metadata.items()),
            )
            _insert_catalog(connection, source)
        connection.execute("VACUUM")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SeedError(f"database integrity check failed: {integrity}")
    finally:
        connection.close()

    database_sha = sha256_bytes(database_path.read_bytes())
    manifest = {
        "schema": "universe.official-development-seed-manifest.v0",
        "seed_id": source["seed_id"],
        "version": source["version"],
        "status": source["status"],
        "source": display_path(source_path),
        "source_sha256": source_sha,
        "database": display_path(database_path),
        "database_sha256": database_sha,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _insert_catalog(connection: sqlite3.Connection, source: dict[str, Any]) -> None:
    for kind in sorted(source["project_kinds"], key=lambda item: item["id"]):
        connection.execute(
            "INSERT INTO project_kind VALUES (?, ?, ?)",
            (kind["id"], kind["label"], kind["description"]),
        )
        connection.executemany(
            "INSERT INTO project_kind_keyword VALUES (?, ?)",
            [(kind["id"], value.lower()) for value in sorted(set(kind["keywords"]))],
        )

    for technology in sorted(source["technologies"], key=lambda item: item["id"]):
        connection.execute(
            "INSERT INTO technology VALUES (?, ?, ?)",
            (technology["id"], technology["label"], technology["category"]),
        )
        aliases = set(technology["aliases"]) | {technology["id"], technology["label"].lower()}
        connection.executemany(
            "INSERT INTO technology_alias VALUES (?, ?)",
            [(technology["id"], value.lower()) for value in sorted(aliases)],
        )

    for failure in sorted(source["failure_patterns"], key=lambda item: item["id"]):
        connection.execute(
            "INSERT INTO failure_pattern VALUES (?, ?, ?, ?)",
            (failure["id"], failure["title"], failure["description"], failure["response"]),
        )
        connection.executemany(
            "INSERT INTO failure_signal VALUES (?, ?)",
            [(failure["id"], value) for value in sorted(set(failure["signals"]))],
        )

    for route in sorted(source["routes"], key=lambda item: item["id"]):
        connection.execute(
            "INSERT INTO route VALUES (?, ?, ?, 'CURATED_HYPOTHESIS')",
            (route["id"], route["title"], route["description"]),
        )
        connection.executemany(
            "INSERT INTO route_project_kind VALUES (?, ?)",
            [(route["id"], value) for value in sorted(set(route["project_kinds"]))],
        )
        connection.executemany(
            "INSERT INTO route_technology_signal VALUES (?, ?)",
            [(route["id"], value) for value in sorted(set(route["technology_signals"]))],
        )
        connection.executemany(
            "INSERT INTO route_goal_signal VALUES (?, ?)",
            [(route["id"], value.lower()) for value in sorted(set(route["goal_signals"]))],
        )
        connection.executemany(
            "INSERT INTO route_failure_pattern VALUES (?, ?)",
            [(route["id"], value) for value in sorted(set(route["failure_refs"]))],
        )
        connection.executemany(
            "INSERT INTO route_step VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    route["id"],
                    ordinal,
                    step["id"],
                    step["title"],
                    step["purpose"],
                    step["exit_evidence"],
                )
                for ordinal, step in enumerate(route["steps"], start=1)
            ],
        )

    for rule in sorted(source["pivot_rules"], key=lambda item: item["id"]):
        connection.execute(
            "INSERT INTO pivot_rule VALUES (?, ?, ?, ?)",
            (rule["id"], rule["trigger"], rule["procedure"], rule["invariant"]),
        )


def inspect_seed(database_path: Path) -> dict[str, Any]:
    with closing(open_read_only(database_path)) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM release_metadata"))
        count_queries = (
            ("project_kind", "SELECT COUNT(*) FROM project_kind"),
            ("technology", "SELECT COUNT(*) FROM technology"),
            ("route", "SELECT COUNT(*) FROM route"),
            ("route_step", "SELECT COUNT(*) FROM route_step"),
            ("failure_pattern", "SELECT COUNT(*) FROM failure_pattern"),
            ("pivot_rule", "SELECT COUNT(*) FROM pivot_rule"),
        )
        counts = {
            table: connection.execute(query).fetchone()[0]
            for table, query in count_queries
        }
    return {"metadata": metadata, "counts": counts}


def suggest_paths(
    database_path: Path,
    project: str,
    kind: str,
    technologies: list[str],
    goal: str,
    limit: int = 3,
) -> dict[str, Any]:
    if not project.strip() or not goal.strip():
        raise SeedError("project and goal must be non-empty")
    if limit < 1:
        raise SeedError("limit must be positive")

    with closing(open_read_only(database_path)) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM release_metadata"))
        kind_id = resolve_kind(connection, kind, f"{project} {goal}")
        technology_ids = resolve_technologies(connection, technologies)
        goal_tokens = tokenize(goal)
        route_rows = connection.execute(
            "SELECT id, title, description, support_level FROM route ORDER BY id"
        ).fetchall()
        candidates = []
        for route_id, title, description, support_level in route_rows:
            route_kinds = set(
                row[0]
                for row in connection.execute(
                    "SELECT project_kind_id FROM route_project_kind WHERE route_id = ?",
                    (route_id,),
                )
            )
            route_tech = set(
                row[0]
                for row in connection.execute(
                    "SELECT technology_id FROM route_technology_signal WHERE route_id = ?",
                    (route_id,),
                )
            )
            route_goals = set(
                row[0]
                for row in connection.execute(
                    "SELECT signal FROM route_goal_signal WHERE route_id = ?",
                    (route_id,),
                )
            )
            kind_match = kind_id in route_kinds
            tech_matches = sorted(technology_ids & route_tech)
            goal_matches = sorted(goal_tokens & route_goals)
            score = (6 if kind_match else 0) + len(tech_matches) * 3 + len(goal_matches) * 2
            if route_id == "validated-software-foundation":
                score += 1
            steps = [
                {
                    "id": row[0],
                    "title": row[1],
                    "purpose": row[2],
                    "exit_evidence": row[3],
                }
                for row in connection.execute(
                    "SELECT id, title, purpose, exit_evidence FROM route_step WHERE route_id = ? ORDER BY ordinal",
                    (route_id,),
                )
            ]
            risks = [
                {
                    "id": row[0],
                    "title": row[1],
                    "response": row[2],
                }
                for row in connection.execute(
                    """
                    SELECT f.id, f.title, f.response
                    FROM route_failure_pattern rfp
                    JOIN failure_pattern f ON f.id = rfp.failure_id
                    WHERE rfp.route_id = ?
                    ORDER BY f.id
                    """,
                    (route_id,),
                )
            ]
            candidates.append(
                {
                    "route_id": route_id,
                    "title": title,
                    "description": description,
                    "rank_score": score,
                    "support_level": support_level,
                    "matches": {
                        "project_kind": kind_id if kind_match else None,
                        "technologies": tech_matches,
                        "goal_signals": goal_matches,
                    },
                    "steps": steps,
                    "risk_patterns": risks,
                }
            )
        candidates.sort(key=lambda item: (-item["rank_score"], item["route_id"]))

    return {
        "schema": RESULT_SCHEMA,
        "status": "FUTURE_PATH_CANDIDATES",
        "seed": {
            "seed_id": metadata["seed_id"],
            "version": metadata["version"],
            "source_sha256": metadata["source_sha256"],
            "evidence_level": metadata["claim.evidence_level"],
        },
        "input": {
            "project": project.strip(),
            "project_kind": kind_id,
            "technologies": sorted(technology_ids),
            "unresolved_technologies": sorted(
                value for value in technologies if normalize(value) not in technology_ids
            ),
            "goal": goal.strip(),
        },
        "candidates": candidates[:limit],
        "effects": {
            "decision": "NONE",
            "current_anchor": "NONE",
            "authority": "NONE",
            "assignment": "NONE",
            "execution": "NONE",
        },
        "probabilities": "NOT_AVAILABLE",
        "next_operation": "USER_SELECTION_REQUIRED",
    }


def resolve_kind(connection: sqlite3.Connection, requested: str, text: str) -> str:
    requested = normalize(requested)
    known = {row[0] for row in connection.execute("SELECT id FROM project_kind")}
    if requested:
        if requested not in known:
            raise SeedError(f"unknown project kind: {requested}")
        return requested
    tokens = tokenize(text)
    matches: dict[str, int] = {}
    for kind_id, keyword in connection.execute(
        "SELECT project_kind_id, keyword FROM project_kind_keyword"
    ):
        if keyword in tokens:
            matches[kind_id] = matches.get(kind_id, 0) + 1
    if not matches:
        return "generic-software"
    return sorted(matches, key=lambda item: (-matches[item], item))[0]


def resolve_technologies(connection: sqlite3.Connection, values: list[str]) -> set[str]:
    aliases = {
        alias: technology_id
        for technology_id, alias in connection.execute(
            "SELECT technology_id, alias FROM technology_alias"
        )
    }
    return {aliases[value] for item in values if (value := normalize(item)) in aliases}


def open_read_only(database_path: Path) -> sqlite3.Connection:
    if not database_path.exists():
        raise SeedError(f"seed database does not exist: {database_path}")
    return sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)


def normalize(value: str) -> str:
    return value.strip().lower()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def tokenize(value: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(value)}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Build and query the Universe development seed")
    commands = root.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build")
    build.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    build.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    build.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)

    inspect = commands.add_parser("inspect")
    inspect.add_argument("--database", type=Path, default=DEFAULT_DATABASE)

    suggest = commands.add_parser("suggest")
    suggest.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    suggest.add_argument("--project", required=True)
    suggest.add_argument("--kind", default="")
    suggest.add_argument("--tech", nargs="*", default=[])
    suggest.add_argument("--goal", required=True)
    suggest.add_argument("--limit", type=int, default=3)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "build":
            result = build_seed(args.source, args.database, args.manifest)
        elif args.command == "inspect":
            result = inspect_seed(args.database)
        else:
            result = suggest_paths(
                args.database,
                args.project,
                args.kind,
                args.tech,
                args.goal,
                args.limit,
            )
    except (OSError, sqlite3.Error, json.JSONDecodeError, SeedError) as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
