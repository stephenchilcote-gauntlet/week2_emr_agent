"""Ground surviving mutmut mutants in code context and classify relevance."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


NOISE = "ignore_noise"
MUST_FIX = "must_fix_behavior"
MANUAL = "manual_review"


@dataclass(frozen=True)
class MutantRecord:
    mutant_id: int
    status: str
    filename: str
    line_number: int
    source_line: str
    mutation_index: int


@dataclass(frozen=True)
class ScopeInfo:
    scope_type: str
    scope_name: str
    node_type: str
    module_level_constant: bool


def parse_mutmut_show_output(output: str) -> tuple[str | None, str | None]:
    """Return (removed_line, added_line) from ``mutmut show`` output."""
    removed: list[str] = []
    added: list[str] = []
    for raw in output.splitlines():
        if raw.startswith("---") or raw.startswith("+++") or raw.startswith("@@"):
            continue
        if raw.startswith("-"):
            removed.append(raw[1:].strip())
        elif raw.startswith("+"):
            added.append(raw[1:].strip())
    removed_line = " ".join(removed) if removed else None
    added_line = " ".join(added) if added else None
    return removed_line, added_line


class ContextResolver:
    """Resolve AST scope information for source line numbers."""

    def __init__(self) -> None:
        self._tree_cache: dict[str, ast.AST] = {}
        self._node_cache: dict[str, list[ast.AST]] = {}

    def resolve(self, filename: str, line_number: int) -> ScopeInfo:
        try:
            tree = self._load_tree(filename)
        except Exception:
            return ScopeInfo(
                scope_type="unknown",
                scope_name="unknown",
                node_type="unknown",
                module_level_constant=False,
            )

        path = self._scope_path(tree, filename, line_number)
        if not path:
            return ScopeInfo(
                scope_type="module",
                scope_name="<module>",
                node_type="module",
                module_level_constant=False,
            )

        innermost = path[0]
        function = next(
            (
                node
                for node in reversed(path)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            None,
        )
        klass = next(
            (node for node in reversed(path) if isinstance(node, ast.ClassDef)),
            None,
        )
        if function:
            scope_type = "function"
            scope_name = getattr(function, "name", "<function>")
        elif klass:
            scope_type = "class"
            scope_name = getattr(klass, "name", "<class>")
        else:
            scope_type = "module"
            scope_name = "<module>"

        return ScopeInfo(
            scope_type=scope_type,
            scope_name=scope_name,
            node_type=type(innermost).__name__,
            module_level_constant=self._is_module_level_constant(path),
        )

    def _load_tree(self, filename: str) -> ast.AST:
        if filename in self._tree_cache:
            return self._tree_cache[filename]
        source = Path(filename).read_text()
        tree = ast.parse(source, filename=filename)
        self._tree_cache[filename] = tree
        self._node_cache[filename] = list(ast.walk(tree))
        return tree

    def _scope_path(self, tree: ast.AST, filename: str, line_number: int) -> list[ast.AST]:
        nodes = self._node_cache.get(filename)
        if nodes is None:
            nodes = list(ast.walk(tree))
            self._node_cache[filename] = nodes

        containing: list[ast.AST] = []
        for node in nodes:
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is None:
                continue
            if end is None:
                end = start
            if start <= line_number <= end:
                containing.append(node)

        def span(node: ast.AST) -> int:
            start = getattr(node, "lineno", line_number)
            end = getattr(node, "end_lineno", start)
            return int(end) - int(start)

        containing.sort(key=span)
        path = containing
        return path

    @staticmethod
    def _is_module_level_constant(path: list[ast.AST]) -> bool:
        if not path:
            return False
        if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in path):
            return False
        for node in path:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        return True
            if isinstance(node, ast.AnnAssign):
                target = node.target
                if isinstance(target, ast.Name) and target.id.isupper():
                    return True
        return False


def classify_mutant(
    record: MutantRecord,
    scope: ScopeInfo,
    removed_line: str | None,
    added_line: str | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    original = (removed_line or record.source_line or "").strip()
    mutated = (added_line or "").strip()

    if scope.module_level_constant:
        reasons.append("top-level ALL_CAPS constant assignment")
        if _is_literal_assignment(original):
            reasons.append("literal/config constant mutation unlikely to reflect runtime logic bug")
            return NOISE, reasons

    if _is_message_only_mutation(original, mutated):
        reasons.append("message/log text changed without logic signal")
        return NOISE, reasons

    if scope.scope_type == "module" and _looks_like_config_line(original):
        reasons.append("module-level config tweak")
        return NOISE, reasons

    if scope.node_type in {
        "If",
        "Compare",
        "BoolOp",
        "Return",
        "Call",
        "BinOp",
        "UnaryOp",
        "While",
        "For",
    }:
        reasons.append(f"mutation hits executable {scope.node_type} logic")
        return MUST_FIX, reasons

    if scope.scope_type == "function" and _looks_like_logic_line(original):
        reasons.append("function-level line contains logic/control-flow tokens")
        return MUST_FIX, reasons

    reasons.append("could not prove this is harmless; review manually")
    return MANUAL, reasons


def _is_literal_assignment(line: str) -> bool:
    match = re.match(r"^[A-Z_][A-Z0-9_]*\s*=\s*(.+)$", line)
    if not match:
        return False
    rhs = match.group(1).strip()
    if rhs.startswith(("{", "[", "(", "f\"", "f'")):
        return False
    return bool(re.match(r"^[-+]?\d+(\.\d+)?$", rhs) or rhs in {"True", "False", "None"} or re.match(r"^['\"].*['\"]$", rhs))


def _looks_like_config_line(line: str) -> bool:
    lowered = line.lower()
    if "=" not in line:
        return False
    keywords = ["timeout", "max_", "min_", "model", "url", "endpoint", "cache", "retry"]
    return any(k in lowered for k in keywords)


def _looks_like_logic_line(line: str) -> bool:
    tokens = ["if ", "elif ", "return ", " and ", " or ", " not ", " for ", " while "]
    ops = ["==", "!=", "<=", ">=", "<", ">"]
    return any(token in line for token in tokens) or any(op in line for op in ops)


def _is_message_only_mutation(original: str, mutated: str) -> bool:
    if not original or not mutated:
        return False
    candidate = any(word in original for word in ("logger", "print(", "message=", "detail="))
    if not candidate:
        return False
    stripped_original = re.sub(r"['\"][^'\"]*['\"]", "<str>", original)
    stripped_mutated = re.sub(r"['\"][^'\"]*['\"]", "<str>", mutated)
    return stripped_original == stripped_mutated


class MutantAnalyzer:
    def __init__(
        self,
        cache_path: str | Path = ".mutmut-cache",
        mutmut_cmd: list[str] | None = None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.mutmut_cmd = mutmut_cmd or [
            "uv",
            "run",
            "--with",
            "mutmut==2.4.4",
            "mutmut",
        ]
        self.context = ContextResolver()

    def analyze(self, statuses: list[str]) -> dict[str, Any]:
        records = self._load_records(statuses)
        analyzed: list[dict[str, Any]] = []
        for record in records:
            removed, added = self._show_mutation(record.mutant_id)
            scope = self.context.resolve(record.filename, record.line_number)
            classification, reasons = classify_mutant(record, scope, removed, added)
            analyzed.append(
                {
                    "mutant_id": record.mutant_id,
                    "status": record.status,
                    "file": record.filename,
                    "line": record.line_number,
                    "scope": scope.scope_type,
                    "scope_name": scope.scope_name,
                    "node_type": scope.node_type,
                    "classification": classification,
                    "reasons": reasons,
                    "original_line": removed or record.source_line,
                    "mutated_line": added,
                }
            )

        counts = {
            MUST_FIX: 0,
            NOISE: 0,
            MANUAL: 0,
        }
        for item in analyzed:
            counts[item["classification"]] += 1

        must_fix_ids = [
            item["mutant_id"]
            for item in analyzed
            if item["classification"] == MUST_FIX
        ]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cache_path": str(self.cache_path),
            "statuses": statuses,
            "total": len(analyzed),
            "counts": counts,
            "must_fix_ids": must_fix_ids,
            "mutants": analyzed,
        }

    def _load_records(self, statuses: list[str]) -> list[MutantRecord]:
        if not self.cache_path.exists():
            raise FileNotFoundError(
                f"{self.cache_path} not found. Run mutmut first to populate cache."
            )

        placeholders = ", ".join("?" for _ in statuses)
        query = (
            "SELECT m.id, m.status, sf.filename, l.line_number, l.line, m.`index` "
            "FROM Mutant m "
            "JOIN Line l ON m.line = l.id "
            "JOIN SourceFile sf ON l.sourcefile = sf.id "
            f"WHERE m.status IN ({placeholders}) "
            "ORDER BY sf.filename, l.line_number, m.id"
        )
        with sqlite3.connect(self.cache_path) as conn:
            rows = conn.execute(query, statuses).fetchall()

        return [
            MutantRecord(
                mutant_id=int(row[0]),
                status=str(row[1]),
                filename=str(row[2]),
                line_number=int(row[3]),
                source_line=str(row[4]),
                mutation_index=int(row[5]),
            )
            for row in rows
        ]

    def _show_mutation(self, mutant_id: int) -> tuple[str | None, str | None]:
        proc = subprocess.run(
            [*self.mutmut_cmd, "show", str(mutant_id)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            raise RuntimeError(f"mutmut show {mutant_id} failed: {stderr}")
        return parse_mutmut_show_output(proc.stdout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify surviving mutants by behavioral relevance"
    )
    parser.add_argument(
        "--cache",
        default=".mutmut-cache",
        help="Path to mutmut sqlite cache",
    )
    parser.add_argument(
        "--status",
        action="append",
        default=["survived"],
        help="Mutmut status to include (repeatable)",
    )
    parser.add_argument(
        "--output",
        default="mutant-analysis.json",
        help="Write JSON report to this path",
    )
    parser.add_argument(
        "--mutmut-cmd",
        default="uv run --with mutmut==2.4.4 mutmut",
        help="Command used to invoke mutmut show",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    mutmut_cmd = args.mutmut_cmd.split()
    status_aliases = {
        "survived": "bad_survived",
        "killed": "ok_killed",
        "timeout": "bad_timeout",
        "suspicious": "bad_suspicious",
        "skipped": "skipped",
        "untested": "untested",
    }
    statuses = [status_aliases.get(s, s) for s in args.status]
    statuses = list(dict.fromkeys(statuses))

    analyzer = MutantAnalyzer(cache_path=args.cache, mutmut_cmd=mutmut_cmd)
    report = analyzer.analyze(statuses)

    output_path = Path(args.output)
    output_path.write_text(json.dumps(report, indent=2))

    print(f"Analyzed {report['total']} mutants -> {output_path}")
    print(
        "  must_fix_behavior={must_fix} ignore_noise={noise} manual_review={manual}".format(
            must_fix=report["counts"][MUST_FIX],
            noise=report["counts"][NOISE],
            manual=report["counts"][MANUAL],
        )
    )
    if report["must_fix_ids"]:
        ids = " ".join(str(i) for i in report["must_fix_ids"])
        print(f"  must_fix_ids: {ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
