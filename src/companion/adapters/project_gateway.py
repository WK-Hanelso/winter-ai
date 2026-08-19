"""Filesystem adapter for Winter's typed read-only Worker gateway."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import NoReturn

from companion.work_orchestration import WorkerTaskPacket
from companion.worker_gateway import (
    GatewayAuditEntry,
    GatewayDeniedError,
    GatewayEvidence,
    GatewayToolResult,
    ProjectToolCall,
)

_OPERATIONS = frozenset({"list_files", "search_text", "read_text"})
_TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".md",
        ".py",
        ".sh",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_MAX_FILE_BYTES = 512_000
_MAX_READ_LINES = 200
_MAX_SEARCH_HITS = 50
_MAX_QUERY_CHARACTERS = 200


class ProjectReadGateway:
    """Expose only bounded text inspection over one immutable project snapshot.

    The Worker cannot submit shell commands, URLs or arbitrary tools through this
    contract. The caller owns provider transport; this adapter owns local data
    access and fails closed when the authorized snapshot changes.
    """

    def __init__(self, root: Path, packet: WorkerTaskPacket) -> None:
        if packet.capabilities != ("project.inspect",):
            raise GatewayDeniedError(
                "gateway requires exactly the project.inspect capability"
            )
        if packet.resources != ("project",):
            raise GatewayDeniedError("gateway requires exactly the project resource")
        self._root = root.resolve(strict=True)
        if not self._root.is_dir():
            raise GatewayDeniedError("gateway project root must be a directory")
        self._snapshot = self._scan_snapshot()
        if not self._snapshot:
            raise GatewayDeniedError("gateway project snapshot has no safe text files")
        self._audit: list[GatewayAuditEntry] = []
        self._evidence: dict[str, GatewayEvidence] = {}
        self._call_ids: set[str] = set()

    @property
    def audit_log(self) -> tuple[GatewayAuditEntry, ...]:
        return tuple(self._audit)

    @property
    def evidence_catalog(self) -> tuple[GatewayEvidence, ...]:
        return tuple(self._evidence.values())

    def execute(self, call: ProjectToolCall) -> GatewayToolResult:
        self._authorize_call(call)
        try:
            current_snapshot = self._scan_snapshot()
        except OSError:
            self._deny(call, "project_snapshot_unavailable")
        if current_snapshot != self._snapshot:
            self._deny(call, "project_snapshot_changed")
        if call.operation == "list_files":
            result = GatewayToolResult(
                call.call_id,
                call.operation,
                "\n".join(self._snapshot),
                (),
            )
        elif call.operation == "search_text":
            result = self._search(call)
        else:
            result = self._read(call)
        for item in result.evidence:
            self._evidence[item.evidence_id] = item
        self._call_ids.add(call.call_id)
        self._audit.append(
            GatewayAuditEntry(
                call.call_id,
                call.operation,
                "allowed",
                "policy_allowed",
                tuple(item.evidence_id for item in result.evidence),
            )
        )
        return result

    def _authorize_call(self, call: ProjectToolCall) -> None:
        if call.call_id in self._call_ids:
            self._deny(call, "duplicate_call_id")
        if call.operation not in _OPERATIONS:
            self._deny(call, "operation_not_allowed")
        if call.operation == "list_files":
            if any(
                value is not None
                for value in (
                    call.path,
                    call.query,
                    call.start_line,
                    call.end_line,
                )
            ):
                self._deny(call, "unexpected_list_argument")
        elif call.operation == "search_text":
            if (
                call.path is not None
                or call.start_line is not None
                or call.end_line is not None
            ):
                self._deny(call, "unexpected_search_argument")
            if call.query is None or not call.query.strip():
                self._deny(call, "search_query_required")
            if len(call.query) > _MAX_QUERY_CHARACTERS:
                self._deny(call, "search_query_too_long")
        else:
            if call.path is None or call.query is not None:
                self._deny(call, "invalid_read_argument")
            if call.start_line is not None and call.start_line < 1:
                self._deny(call, "invalid_start_line")
            if call.end_line is not None and call.end_line < 1:
                self._deny(call, "invalid_end_line")
            if (
                call.start_line is not None
                and call.end_line is not None
                and call.end_line < call.start_line
            ):
                self._deny(call, "invalid_line_range")

    def _search(self, call: ProjectToolCall) -> GatewayToolResult:
        assert call.query is not None
        query = call.query.casefold()
        rendered: list[str] = []
        evidence: list[GatewayEvidence] = []
        for relative in self._snapshot:
            for line_number, line in enumerate(
                self._read_lines(relative, call), start=1
            ):
                if query not in line.casefold():
                    continue
                locator = f"{relative}:{line_number}"
                rendered.append(f"{locator}: {line}")
                evidence.append(_evidence(locator, line))
                if len(evidence) >= _MAX_SEARCH_HITS:
                    break
            if len(evidence) >= _MAX_SEARCH_HITS:
                break
        return GatewayToolResult(
            call.call_id,
            call.operation,
            "\n".join(rendered),
            tuple(evidence),
        )

    def _read(self, call: ProjectToolCall) -> GatewayToolResult:
        assert call.path is not None
        relative = self._safe_relative(call.path, call)
        lines = self._read_lines(relative, call)
        start = call.start_line or 1
        if start > len(lines):
            self._deny(call, "start_line_out_of_range")
        requested_end = call.end_line or start + _MAX_READ_LINES - 1
        end = min(requested_end, start + _MAX_READ_LINES - 1, len(lines))
        selected = lines[start - 1 : end]
        evidence = tuple(
            _evidence(f"{relative}:{line_number}", line)
            for line_number, line in enumerate(selected, start=start)
        )
        content = "\n".join(
            f"{line_number}: {line}"
            for line_number, line in enumerate(selected, start=start)
        )
        return GatewayToolResult(call.call_id, call.operation, content, evidence)

    def _safe_relative(self, value: str, call: ProjectToolCall) -> str:
        if "\\" in value:
            self._deny(call, "invalid_path_separator")
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            self._deny(call, "path_outside_project")
        relative = candidate.as_posix()
        if relative not in self._snapshot:
            self._deny(call, "path_not_in_snapshot")
        path = self._root / relative
        relative_parents = (
            parent for parent in candidate.parents if parent != PurePosixPath(".")
        )
        if path.is_symlink() or any(
            (self._root / parent).is_symlink() for parent in relative_parents
        ):
            self._deny(call, "symlink_not_allowed")
        return relative

    def _read_lines(
        self, relative: str, call: ProjectToolCall
    ) -> tuple[str, ...]:
        try:
            data = (self._root / relative).read_bytes()
        except OSError:
            self._deny(call, "project_snapshot_unavailable")
        if sha256(data).hexdigest() != self._snapshot[relative]:
            self._deny(call, "project_snapshot_changed")
        try:
            return tuple(data.decode("utf-8").splitlines())
        except UnicodeDecodeError:
            self._deny(call, "project_snapshot_changed")

    def _scan_snapshot(self) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for path in sorted(self._root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative_path = path.relative_to(self._root)
            if any(part.startswith(".") for part in relative_path.parts):
                continue
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            data = path.read_bytes()
            if b"\x00" in data:
                continue
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            snapshot[relative_path.as_posix()] = sha256(data).hexdigest()
        return snapshot

    def _deny(self, call: ProjectToolCall, reason: str) -> NoReturn:
        self._call_ids.add(call.call_id)
        self._audit.append(
            GatewayAuditEntry(
                call.call_id,
                call.operation,
                "denied",
                reason,
                (),
            )
        )
        raise GatewayDeniedError(f"project read gateway denied call: {reason}")


def _evidence(locator: str, content: str) -> GatewayEvidence:
    digest = sha256(content.encode("utf-8")).hexdigest()
    identity = sha256(f"{locator}\0{digest}".encode()).hexdigest()[:16]
    return GatewayEvidence(f"E-{identity}", locator, digest)
