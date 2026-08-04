"""Shared core for every ``mc.py`` command group.

Everything in here is a pure function of its inputs plus the workspace bytes:
workspace resolution and path safety, the ``Result``/``Diagnostic`` envelope,
the ``Ident`` guard, the runtime-prerequisite guard, YAML/Markdown I/O with a
fixed key order, the injected clock, and the schema-validation front end.

Three rules this module exists to enforce:

* **Reject, never sanitize.** A value that fails ``Ident`` produces an
  ``E_BAD_IDENT`` diagnostic. There is deliberately no function here that
  rewrites a rejected value into an accepted one.
* **Nothing escapes the workspace.** Every path is resolved (symlinks and all)
  and required to sit inside ``Workspace.root``.
* **No uncaught traceback.** Failures are ``ToolError``, carrying a
  ``Diagnostic`` with a stable machine-matchable ``code``.

See ``context/ai-plugins/spec/TOOLS/`` for the owning specification.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Error codes -- stable and machine-matchable.
# ---------------------------------------------------------------------------

# Codes that force exit 2 (usage / refusal / environment).
E_USAGE = "E_USAGE"
E_BAD_IDENT = "E_BAD_IDENT"
E_PATH_ESCAPE = "E_PATH_ESCAPE"
E_MISSING_PREREQ = "E_MISSING_PREREQ"
E_UNKNOWN_KIND = "E_UNKNOWN_KIND"
E_PARSE = "E_PARSE"
E_UNSUPPORTED_INPUT = "E_UNSUPPORTED_INPUT"
E_READ = "E_READ"
E_GROUP_UNAVAILABLE = "E_GROUP_UNAVAILABLE"

# Codes a command that *ran* reports (exit 1).
E_NO_SUCH_FILE = "E_NO_SUCH_FILE"
E_SCHEMA_INVALID = "E_SCHEMA_INVALID"
E_NOT_FOUND = "E_NOT_FOUND"
E_AMBIGUOUS = "E_AMBIGUOUS"
E_INVALID_STATE = "E_INVALID_STATE"
E_DANGLING_DEPENDS_ON = "E_DANGLING_DEPENDS_ON"
E_COUPLING = "E_COUPLING"
E_MISSING_REQUIREMENT = "E_MISSING_REQUIREMENT"
E_ORPHAN_REQUIREMENT = "E_ORPHAN_REQUIREMENT"
E_HANDOFF = "E_HANDOFF"
E_UNPARSED_NAME = "E_UNPARSED_NAME"

#: Every code above that maps to exit 2 rather than exit 1.
USAGE_CODES = frozenset(
    {
        E_USAGE,
        E_BAD_IDENT,
        E_PATH_ESCAPE,
        E_MISSING_PREREQ,
        E_UNKNOWN_KIND,
        E_PARSE,
        E_UNSUPPORTED_INPUT,
        E_READ,
        E_GROUP_UNAVAILABLE,
    }
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnostic:
    """One failure or warning, per TOOLS-DATAMODEL.md."""

    severity: str
    code: str
    message: str
    file: Optional[str] = None  # workspace-relative
    line: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.file is not None:
            out["file"] = self.file
        if self.line is not None:
            out["line"] = self.line
        return out

    def render(self) -> str:
        prefix = ""
        if self.file is not None:
            prefix = "%s: " % self.file
            if self.line is not None:
                prefix = "%s:%d: " % (self.file, self.line)
        return "%s: %s%s" % (self.severity, prefix, self.message)


def error(code: str, message: str, file: Optional[str] = None, line: Optional[int] = None) -> Diagnostic:
    return Diagnostic(SEVERITY_ERROR, code, message, file, line)


def warning(code: str, message: str, file: Optional[str] = None, line: Optional[int] = None) -> Diagnostic:
    return Diagnostic(SEVERITY_WARNING, code, message, file, line)


@dataclass
class Result:
    """The envelope every subcommand returns, per TOOLS-DATAMODEL.md.

    ``ok`` is a read-only property rather than a stored field so the documented
    invariant -- ``ok == false`` iff any diagnostic has severity ``error`` --
    holds by construction and cannot drift out of step with ``diagnostics``.
    It is emitted first in ``to_json()``, matching the documented field order
    ``ok, command, data, diagnostics``.
    """

    command: str
    data: Any = None
    diagnostics: List[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.severity == SEVERITY_ERROR for d in self.diagnostics)

    def add(self, diagnostic: Diagnostic) -> "Result":
        self.diagnostics.append(diagnostic)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "data": self.data,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }

    def to_json(self) -> str:
        """The ``--json`` envelope, emitted verbatim on stdout."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"

    def render(self) -> Tuple[str, str]:
        """Human rendering: ``(stdout_text, stderr_text)``.

        ``data`` becomes readable lines on stdout and ``diagnostics`` become
        ``error:``/``warning:`` lines on stderr.
        """
        out_lines = render_data(self.data)
        err_lines = [d.render() for d in self.diagnostics]
        out = "".join(line + "\n" for line in out_lines)
        err = "".join(line + "\n" for line in err_lines)
        return out, err


def render_data(data: Any) -> List[str]:
    """Render a ``Result.data`` payload as human-readable lines.

    A group whose human output has a fixed documented format supplies it as
    ``data["lines"]`` (a list of strings) and those lines are emitted verbatim;
    otherwise the payload is rendered generically in its own key order.
    """
    if data is None:
        return []
    if isinstance(data, dict):
        lines = data.get("lines")
        if isinstance(lines, list) and all(isinstance(item, str) for item in lines):
            return list(lines)
    return _generic_lines(data, 0)


def _generic_lines(value: Any, depth: int) -> List[str]:
    pad = "  " * depth
    if isinstance(value, dict):
        lines: List[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append("%s%s:" % (pad, key))
                lines.extend(_generic_lines(item, depth + 1))
            else:
                lines.append("%s%s: %s" % (pad, key, _scalar(item)))
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append("%s-" % pad)
                lines.extend(_generic_lines(item, depth + 1))
            else:
                lines.append("%s- %s" % (pad, _scalar(item)))
        return lines
    return ["%s%s" % (pad, _scalar(value))]


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def exit_code(result: Result) -> int:
    """Map a ``Result`` onto the documented exit codes 0/1/2."""
    for diagnostic in result.diagnostics:
        if diagnostic.severity == SEVERITY_ERROR and diagnostic.code in USAGE_CODES:
            return EXIT_USAGE
    return EXIT_OK if result.ok else EXIT_ERROR


def emit(result: Result, json_out: bool = False, stdout=None, stderr=None) -> None:
    """Write a ``Result`` to the streams. All non-envelope output is stderr."""
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    if json_out:
        out.write(result.to_json())
        return
    rendered_out, rendered_err = result.render()
    if rendered_out:
        out.write(rendered_out)
    if rendered_err:
        err.write(rendered_err)


class ToolError(Exception):
    """A failure carrying its ``Diagnostic``. Never surfaces as a traceback."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class FileMissing(ToolError):
    """A requested file does not exist. Fails that file alone (exit 1)."""

    def __init__(self, path: Any, display: Optional[str] = None) -> None:
        shown = display if display is not None else str(path)
        super().__init__(error(E_NO_SUCH_FILE, "no such file", file=shown))
        self.path = str(path)


def fail(code: str, message: str, file: Optional[str] = None, line: Optional[int] = None) -> "ToolError":
    return ToolError(error(code, message, file, line))


# ---------------------------------------------------------------------------
# Ident -- rejected, never sanitized
# ---------------------------------------------------------------------------

IDENT_PATTERN = r"^[A-Za-z0-9._-]+$"
_IDENT_RE = re.compile(IDENT_PATTERN)


def is_ident(value: Any) -> bool:
    """True iff ``value`` matches ``/^[A-Za-z0-9._-]+$/``."""
    return isinstance(value, str) and _IDENT_RE.fullmatch(value) is not None


def require_ident(value: Any, what: str = "identifier") -> Optional[Diagnostic]:
    """Return ``None`` when ``value`` is a valid ``Ident``, else an
    ``E_BAD_IDENT`` diagnostic.

    This never returns a corrected value, and no caller may construct one:
    silently rewriting a bad identifier is how a path escape becomes a
    valid-looking file.
    """
    if is_ident(value):
        return None
    return error(
        E_BAD_IDENT,
        "invalid %s %r: must match /%s/ (rejected, never sanitized)"
        % (what, value, IDENT_PATTERN.strip("^$")),
    )


def check_ident(value: Any, what: str = "identifier") -> str:
    """``require_ident`` in raising form. Returns ``value`` unchanged."""
    diagnostic = require_ident(value, what)
    if diagnostic is not None:
        raise ToolError(diagnostic)
    return value


# ---------------------------------------------------------------------------
# Injected clock
# ---------------------------------------------------------------------------

Instant = str  # ISO-8601 date, per TOOLS-DATAMODEL.md

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def system_instant() -> Instant:
    """The only wall-clock read in the package. Used when ``--now`` is absent."""
    return date.today().isoformat()


def parse_instant(raw: str) -> Instant:
    """Parse ``--now`` into an ``Instant`` (an ISO-8601 date)."""
    if not isinstance(raw, str) or not raw.strip():
        raise fail(E_USAGE, "--now requires an ISO-8601 date, got %r" % (raw,))
    text = raw.strip()
    if _DATE_RE.match(text):
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            raise fail(E_USAGE, "--now is not a valid ISO-8601 date: %r" % (raw,))
        return text
    head = text.split("T", 1)[0].split(" ", 1)[0]
    if _DATE_RE.match(head):
        try:
            datetime.strptime(head, "%Y-%m-%d")
        except ValueError:
            raise fail(E_USAGE, "--now is not a valid ISO-8601 date: %r" % (raw,))
        return head
    raise fail(E_USAGE, "--now is not a valid ISO-8601 date: %r" % (raw,))


def resolve_instant(raw: Optional[str]) -> Instant:
    """``--now`` when given, else the system clock. Called once per run."""
    if raw is None:
        return system_instant()
    return parse_instant(raw)


# ---------------------------------------------------------------------------
# Workspace and path safety
# ---------------------------------------------------------------------------


class Workspace:
    """The root every read and write is confined to, per TOOLS-DATAMODEL.md."""

    def __init__(self, root: Any) -> None:
        self.root = Path(root).expanduser().resolve()

    @classmethod
    def resolve(cls, raw: Optional[str] = None) -> "Workspace":
        """From ``--workspace`` when given, else the current working directory."""
        return cls(raw if raw else Path.cwd())

    # -- derived listings, always sorted -----------------------------------
    @property
    def repos(self) -> List[str]:
        return self._listing(self.root / "repos")

    @property
    def targets(self) -> List[str]:
        return [name for name in self._listing(self.root / "context") if name != "project"]

    @staticmethod
    def _listing(directory: Path) -> List[str]:
        if not directory.is_dir():
            return []
        return sorted(
            entry.name
            for entry in directory.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )

    # -- path safety --------------------------------------------------------
    def safe_path(self, *parts: Any) -> Path:
        """Join ``parts`` under the root, resolve symlinks, and refuse escapes.

        Raises ``ToolError`` carrying ``E_PATH_ESCAPE`` for anything that lands
        outside ``root`` after resolution -- including ``..`` traversal, an
        absolute component, and a symlink pointing out of the tree.
        """
        path, diagnostic = self.resolve_path(*parts)
        if diagnostic is not None:
            raise ToolError(diagnostic)
        return path

    def resolve_path(self, *parts: Any) -> Tuple[Optional[Path], Optional[Diagnostic]]:
        """Non-raising form of :meth:`safe_path`."""
        if not parts:
            return self.root, None
        candidate = self.root
        shown: List[str] = []
        for part in parts:
            if part is None or part == "":
                return None, error(E_USAGE, "empty path component in %r" % (parts,))
            text = str(part)
            shown.append(text)
            candidate = candidate / text
        joined = "/".join(shown)
        resolved = Path(candidate).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            return None, error(
                E_PATH_ESCAPE,
                "path %r resolves to %s, outside the workspace root %s"
                % (joined, resolved, self.root),
            )
        return resolved, None

    def rel(self, path: Any) -> str:
        """``path`` expressed relative to the root, for diagnostics."""
        try:
            resolved = Path(path).resolve()
        except OSError:
            return str(path)
        try:
            return str(resolved.relative_to(self.root))
        except ValueError:
            return str(path)

    def to_dict(self) -> Dict[str, Any]:
        return {"root": str(self.root), "repos": self.repos, "targets": self.targets}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Workspace(root=%r)" % (str(self.root),)


# ---------------------------------------------------------------------------
# Runtime prerequisites -- one checked entry point, never an ImportError
# ---------------------------------------------------------------------------

#: module name -> distribution name, in the order they are reported.
PREREQUISITES = (("yaml", "PyYAML"), ("jsonschema", "jsonschema"))
INSTALL_COMMAND = "pip install pyyaml jsonschema"


def require_prereq(module_name: str):
    """Import a required third-party package or raise ``E_MISSING_PREREQ``.

    This is the only place ``yaml`` and ``jsonschema`` are imported, so a
    missing package can never reach the caller as an ``ImportError``.
    """
    package = dict(PREREQUISITES).get(module_name, module_name)
    try:
        return importlib.import_module(module_name)
    except ImportError:
        raise fail(
            E_MISSING_PREREQ,
            "required package %r is not installed; install the prerequisites with: %s"
            % (package, INSTALL_COMMAND),
        )


def require_prereqs() -> None:
    """Check every runtime prerequisite up front, so *every* command fails the
    same way when one is absent."""
    for module_name, _package in PREREQUISITES:
        require_prereq(module_name)


# ---------------------------------------------------------------------------
# YAML / Markdown I/O
# ---------------------------------------------------------------------------

#: Effectively infinite, so emitted YAML never line-wraps.
YAML_WIDTH = 1 << 30
YAML_INDENT = 2


def read_text(path: Any, display: Optional[str] = None) -> str:
    shown = display if display is not None else str(path)
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileMissing(path, shown)
    except IsADirectoryError:
        raise fail(E_READ, "is a directory", file=shown)
    except OSError as exc:
        raise fail(E_READ, "%s" % (exc.strerror or exc,), file=shown)


def load_yaml(path: Any, display: Optional[str] = None) -> Any:
    """Parse a YAML document. Malformed input is ``E_PARSE``, never a traceback."""
    shown = display if display is not None else str(path)
    return parse_yaml(read_text(path, shown), shown)


def parse_yaml(text: str, display: str) -> Any:
    yaml = require_prereq("yaml")
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        line = None
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            line = mark.line + 1
        raise fail(E_PARSE, "invalid YAML: %s" % (exc,), file=display, line=line)


def load_json(path: Any, display: Optional[str] = None) -> Any:
    shown = display if display is not None else str(path)
    text = read_text(path, shown)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise fail(E_PARSE, "invalid JSON: %s" % (exc,), file=shown, line=exc.lineno)


def dump_yaml(data: Any, schema: Optional[Dict[str, Any]] = None) -> str:
    """Serialise ``data`` deterministically.

    Key order is the ``schema``'s ``properties`` order when a schema is given --
    not insertion order and not alphabetical -- with fixed indentation and no
    line wrapping, so re-emitting an unchanged document diffs empty.
    """
    yaml = require_prereq("yaml")
    payload = order_by_schema(data, schema) if schema is not None else data
    return yaml.safe_dump(
        payload,
        sort_keys=False,
        default_flow_style=False,
        indent=YAML_INDENT,
        width=YAML_WIDTH,
        allow_unicode=True,
    )


def write_yaml(path: Any, data: Any, schema: Optional[Dict[str, Any]] = None) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_yaml(data, schema), encoding="utf-8")
    return target


def order_by_schema(data: Any, schema: Optional[Dict[str, Any]], root: Optional[Dict[str, Any]] = None) -> Any:
    """Reorder mapping keys to follow the schema's declared property order.

    Keys the schema does not name keep their existing relative order, after the
    named ones. The result is a plain structure ready for ``yaml.safe_dump``.
    """
    if schema is None:
        return data
    if root is None:
        root = schema
    resolved = _deref(schema, root)
    if not isinstance(resolved, dict):
        return data
    if isinstance(data, dict):
        branches = resolved.get("oneOf") or resolved.get("anyOf")
        if branches:
            for branch in branches:
                candidate = _deref(branch, root)
                if not isinstance(candidate, dict):
                    continue
                required = candidate.get("required") or []
                if required and all(key in data for key in required):
                    return order_by_schema(data, candidate, root)
            return data
        properties = resolved.get("properties") or {}
        additional = resolved.get("additionalProperties")
        ordered: Dict[str, Any] = {}
        for key in properties:
            if key in data:
                ordered[key] = order_by_schema(data[key], properties[key], root)
        for key, value in data.items():
            if key in ordered:
                continue
            sub = additional if isinstance(additional, dict) else None
            ordered[key] = order_by_schema(value, sub, root) if sub else value
        return ordered
    if isinstance(data, list):
        items = resolved.get("items")
        if isinstance(items, dict):
            return [order_by_schema(entry, items, root) for entry in data]
        return data
    return data


def _deref(schema: Any, root: Dict[str, Any]) -> Any:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 32:
        schema = _resolve_pointer(schema["$ref"], root)
        seen += 1
    return schema


def _resolve_pointer(ref: str, root: Dict[str, Any]) -> Any:
    if not ref.startswith("#/"):
        return None
    node: Any = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            return None
        node = node[token]
    return node


# -- Markdown front matter ---------------------------------------------------

FRONT_MATTER_RE = re.compile(r"^<!--\s*([A-Za-z0-9_-]+):\s*(.*?)\s*-->$")


def read_front_matter(text: str) -> Dict[str, str]:
    """Pull the leading ``<!-- key: value -->`` block into a dict of strings."""
    matter: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = FRONT_MATTER_RE.match(line)
        if match:
            matter[match.group(1)] = match.group(2)
            continue
        break  # the first non-blank, non-front-matter line ends the block
    return matter


def load_front_matter(path: Any, display: Optional[str] = None) -> Dict[str, str]:
    shown = display if display is not None else str(path)
    return read_front_matter(read_text(path, shown))


def render_front_matter(matter: Dict[str, str]) -> str:
    """Render a front-matter block in the mapping's own key order."""
    return "".join("<!-- %s: %s -->\n" % (key, value) for key, value in matter.items())


def strip_front_matter(text: str) -> str:
    """The document body with its leading front-matter block removed."""
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if FRONT_MATTER_RE.match(line):
            index += 1
            continue
        break
    return "".join(lines[index:])


def write_front_matter(path: Any, matter: Dict[str, str]) -> Path:
    """Replace (or create) the leading front-matter block, keeping the body."""
    target = Path(path)
    body = ""
    if target.exists():
        body = strip_front_matter(target.read_text(encoding="utf-8"))
    block = render_front_matter(matter)
    if body and not body.startswith("\n"):
        block += "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(block + body, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Schema-validation front end
# ---------------------------------------------------------------------------

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

#: The nine canonical schema basenames, per SCHEMAS-INTERFACE.md.
CANONICAL_KINDS = (
    "catalog",
    "change-frontmatter",
    "conformance-report",
    "inconsistency-report",
    "plan-graph",
    "plan-state",
    "project-state",
    "requirements-frontmatter",
    "story-report",
)

#: The seven friendly aliases, per SCHEMAS-INTERFACE.md.
KIND_ALIASES = {
    "change": "change-frontmatter",
    "plan": "plan-graph",
    "ledger": "project-state",
    "conformance": "conformance-report",
    "story": "story-report",
    "inconsistency": "inconsistency-report",
    "requirements": "requirements-frontmatter",
}

SCHEMA_SUFFIX = ".schema.json"
INSTANCE_SUFFIXES = (".yaml", ".yml", ".json", ".md")

_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}


def resolve_kind(kind: str) -> Path:
    """Resolve ``kind`` to a schema document inside the schema directory.

    Accepts a canonical basename, one of the seven aliases, or an explicit
    ``<name>.schema.json``.
    """
    check_ident(kind, "schema kind")
    basename = KIND_ALIASES.get(kind, kind)
    for candidate in (SCHEMA_DIR / (basename + SCHEMA_SUFFIX), SCHEMA_DIR / kind):
        resolved = candidate.resolve()
        if resolved != SCHEMA_DIR and SCHEMA_DIR not in resolved.parents:
            continue  # a kind must never reach outside the schema directory
        if resolved.is_file():
            return resolved
    raise fail(
        E_UNKNOWN_KIND,
        "unknown schema kind %r; available: %s" % (kind, ", ".join(CANONICAL_KINDS)),
    )


def load_schema(kind: str) -> Dict[str, Any]:
    """The parsed schema document for ``kind``."""
    path = resolve_kind(kind)
    key = str(path)
    if key not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[key] = json.loads(path.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE[key]


def load_instance(path: Any, display: Optional[str] = None) -> Any:
    """Load a schema instance: YAML, JSON, or Markdown front matter."""
    shown = display if display is not None else str(path)
    text = read_text(path, shown)
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise fail(E_PARSE, "invalid JSON: %s" % (exc,), file=shown, line=exc.lineno)
    if suffix == ".md":
        return read_front_matter(text)
    if suffix in (".yaml", ".yml"):
        return parse_yaml(text, shown)
    raise fail(
        E_UNSUPPORTED_INPUT,
        "unsupported file extension (want %s)" % ("/".join(INSTANCE_SUFFIXES),),
        file=shown,
    )


def _relocate(exc: ToolError, file: str) -> ToolError:
    """Re-point a diagnostic's ``file`` at its workspace-relative form."""
    diagnostic = exc.diagnostic
    if diagnostic.file is None:
        return exc
    return ToolError(
        Diagnostic(diagnostic.severity, diagnostic.code, diagnostic.message, file, diagnostic.line)
    )


def validate_against(schema: Dict[str, Any], instance: Any) -> List[str]:
    """Validate ``instance`` against ``schema``; return the error lines."""
    jsonschema = require_prereq("jsonschema")
    try:
        jsonschema.validate(instance, schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        return ["%s: %s" % (location, exc.message)]
    except jsonschema.SchemaError as exc:  # pragma: no cover - shipped schemas are valid
        raise fail(E_PARSE, "schema is not valid JSON Schema: %s" % (exc,))
    return []


def validate_instance(kind: str, path: Any, ws: Optional[Workspace] = None) -> Result:
    """Validate one file against ``kind`` and return the ``Result`` envelope.

    The human rendering is the documented ``OK``/``FAIL`` block, carried in
    ``data["lines"]``. A missing file yields an ``E_NO_SUCH_FILE`` diagnostic
    rather than an exception, so a batch caller can fail that file alone; every
    other failure raises ``ToolError`` and aborts the batch with exit 2.
    """
    display = str(path)
    schema = load_schema(kind)
    target = ws.safe_path(path) if ws is not None else Path(path)
    relative = ws.rel(target) if ws is not None else display
    try:
        instance = load_instance(target, display)
    except FileMissing:
        return Result(
            command="validate",
            data={
                "kind": kind,
                "files": [{"path": display, "ok": False, "errors": ["no such file"]}],
                "lines": [],
            },
            diagnostics=[error(E_NO_SUCH_FILE, "no such file", file=relative)],
        )
    except ToolError as exc:
        raise _relocate(exc, relative)
    errors = validate_against(schema, instance)
    if errors:
        lines = ["FAIL  %s (%s):" % (display, kind)]
        lines.extend("        - %s" % message for message in errors)
        return Result(
            command="validate",
            data={
                "kind": kind,
                "files": [{"path": display, "ok": False, "errors": errors}],
                "lines": lines,
            },
            diagnostics=[
                error(E_SCHEMA_INVALID, "does not validate against %s" % (kind,), file=relative)
            ],
        )
    return Result(
        command="validate",
        data={
            "kind": kind,
            "files": [{"path": display, "ok": True, "errors": []}],
            "lines": ["OK    %s (%s)" % (display, kind)],
        },
    )
