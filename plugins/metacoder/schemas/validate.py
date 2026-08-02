#!/usr/bin/env python3
"""Validate a metacoder artifact against its JSON Schema.

Usage:
    python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py <kind> <file> [<file> ...]

<kind> is the schema basename (with or without the .schema.json suffix), one of:
    catalog | change-frontmatter | plan-graph | project-state | plan-state
    conformance-report | story-report | inconsistency-report
or one of six friendly aliases: change, plan, ledger, conformance, story, inconsistency.

Input file type is auto-detected:
    *.yaml / *.yml   -> parsed as YAML: PyYAML's yaml.safe_load if importable, else
                        ruamel.yaml's YAML(typ="safe") if importable, else a vendored
                        restricted-subset loader -- no third-party package is required.
    *.json           -> parsed as JSON
    *.md             -> HTML-comment front-matter (<!-- key: value -->) is extracted
                        into an object (used for change docs)

Exit codes:
    0 = every file validated
    1 = a file failed validation or was not found
    2 = a usage error, an unresolvable <kind>, an unsupported file extension, a malformed
        .json/.yaml/.yml instance, a YAML document outside the vendored loader's accepted
        subset when neither PyYAML nor ruamel.yaml is importable, or a read error other
        than a missing file (e.g. passing a directory)

Every exit-2 load failure prints a `validate.py: <message>` diagnostic on stderr naming
the file and, where the parser reports one, the line -- never a Python traceback.

Validation uses the `jsonschema` package when importable (full Draft 2020-12);
otherwise it falls back to a self-contained validator covering the subset of
JSON Schema these schemas use (type/const/enum/required/properties/
additionalProperties/propertyNames/items/minItems/minProperties/minLength/
minimum/maximum/pattern/oneOf/$ref/if-then).
"""
import json
import os
import re
import sys

SCHEMA_DIR = os.path.dirname(os.path.abspath(__file__))

ALIASES = {
    "change": "change-frontmatter",
    "change-frontmatter": "change-frontmatter",
    "catalog": "catalog",
    "plan-graph": "plan-graph",
    "plan": "plan-graph",
    "project-state": "project-state",
    "ledger": "project-state",
    "plan-state": "plan-state",
    "conformance-report": "conformance-report",
    "conformance": "conformance-report",
    "story-report": "story-report",
    "story": "story-report",
    "inconsistency-report": "inconsistency-report",
    "inconsistency": "inconsistency-report",
    "requirements-frontmatter": "requirements-frontmatter",
    "requirements": "requirements-frontmatter",
}


def load_schema(kind):
    base = ALIASES.get(kind, kind)
    path = os.path.join(SCHEMA_DIR, base + ".schema.json")
    if not os.path.exists(path):
        # allow passing an explicit basename that already has .schema.json
        path = os.path.join(SCHEMA_DIR, kind)
        if not os.path.exists(path):
            die(2, f"unknown schema kind {kind!r}; available: "
                   + ", ".join(sorted(set(ALIASES.values()))))
    with open(path) as fh:
        return json.load(fh)


def extract_frontmatter(text):
    """Pull leading <!-- key: value --> comment lines into a dict of strings."""
    fm = {}
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^<!--\s*([A-Za-z0-9_-]+):\s*(.*?)\s*-->$", s)
        if m:
            fm[m.group(1)] = m.group(2)
            continue
        break  # first non-blank, non-frontmatter line ends the block
    return fm


# --------------------------------------------------------------------------
# Vendored restricted-subset YAML loader (tier three -- used only when
# neither PyYAML nor ruamel.yaml is importable). See SCHEMAS-INTERFACE.md
# "Accepted YAML (vendored loader)" for the accepted subset and rejections.
# --------------------------------------------------------------------------
class YAMLSubsetError(Exception):
    def __init__(self, message, line):
        self.line = line
        super().__init__(message)


def _strip_comment(s):
    """Remove a trailing '#' comment, respecting quoted strings."""
    out = []
    in_squote = False
    in_dquote = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if in_squote:
            out.append(c)
            if c == "'":
                if i + 1 < n and s[i + 1] == "'":
                    out.append(s[i + 1])
                    i += 2
                    continue
                in_squote = False
            i += 1
            continue
        if in_dquote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_dquote = False
            i += 1
            continue
        if c == "'":
            in_squote = True
            out.append(c)
            i += 1
            continue
        if c == '"':
            in_dquote = True
            out.append(c)
            i += 1
            continue
        if c == "#" and (i == 0 or s[i - 1] in " \t"):
            break
        out.append(c)
        i += 1
    return "".join(out)


def _find_colon(s):
    """Index of the ':' that separates a plain key from its value, or None."""
    for i, c in enumerate(s):
        if c == ":" and (i + 1 == len(s) or s[i + 1] == " "):
            return i
    return None


def _read_dquoted(s, lineno):
    out = []
    i = 1
    n = len(s)
    esc = {
        "n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/",
        "r": "\r", "0": "\0", "b": "\b", "f": "\f",
    }
    while i < n:
        c = s[i]
        if c == "\\":
            if i + 1 >= n:
                raise YAMLSubsetError("unterminated escape in double-quoted scalar", lineno)
            nc = s[i + 1]
            out.append(esc.get(nc, nc))
            i += 2
            continue
        if c == '"':
            return "".join(out), s[i + 1:]
        out.append(c)
        i += 1
    raise YAMLSubsetError("unterminated double-quoted scalar", lineno)


def _read_squoted(s, lineno):
    out = []
    i = 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "'":
            if i + 1 < n and s[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), s[i + 1:]
        out.append(c)
        i += 1
    raise YAMLSubsetError("unterminated single-quoted scalar", lineno)


_INT_RE = re.compile(r"^[-+]?(0b[0-1_]+|0x[0-9a-fA-F_]+|0[0-7_]+|(0|[1-9][0-9_]*))$")
_FLOAT_RE = re.compile(r"^[-+]?(\.[0-9]+|[0-9][0-9_]*\.[0-9_]*)([eE][-+]?[0-9]+)?$|^[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+$")


def _try_parse_int(s):
    if not _INT_RE.match(s):
        return None
    neg = s.startswith("-")
    ss = s[1:] if s[0] in "+-" else s
    ss = ss.replace("_", "")
    if ss.startswith("0b"):
        val = int(ss[2:], 2)
    elif ss.startswith("0x"):
        val = int(ss[2:], 16)
    elif ss.startswith("0") and len(ss) > 1:
        val = int(ss, 8)
    else:
        val = int(ss)
    return -val if neg else val


def _try_parse_float(s):
    if s in (".inf", "+.inf", ".Inf", ".INF"):
        return float("inf")
    if s == "-.inf":
        return float("-inf")
    if s in (".nan", ".NaN", ".NAN"):
        return float("nan")
    if not _FLOAT_RE.match(s):
        return None
    try:
        return float(s.replace("_", ""))
    except ValueError:
        return None


def _resolve_plain_scalar(s):
    """Resolve an unquoted scalar the way PyYAML's safe loader would."""
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    int_val = _try_parse_int(s)
    if int_val is not None:
        return int_val
    float_val = _try_parse_float(s)
    if float_val is not None:
        return float_val
    return s


def _split_flow_items(inner):
    """Split a flow sequence's inner text on top-level commas, respecting quotes."""
    parts = []
    cur = []
    in_squote = False
    in_dquote = False
    for c in inner:
        if in_squote:
            cur.append(c)
            if c == "'":
                in_squote = False
            continue
        if in_dquote:
            cur.append(c)
            if c == '"':
                in_dquote = False
            continue
        if c == "'":
            in_squote = True
            cur.append(c)
            continue
        if c == '"':
            in_dquote = True
            cur.append(c)
            continue
        if c == ",":
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(c)
    parts.append("".join(cur))
    return parts


def _parse_flow_sequence(val_str, lineno):
    """A restricted flow sequence of scalars, e.g. [A], [A, B], ["x", 'y'].

    No nested flow collections and no flow mappings -- those stay rejected.
    """
    if not val_str.endswith("]"):
        raise YAMLSubsetError("unterminated flow sequence", lineno)
    inner = val_str[1:-1].strip()
    if inner == "":
        return []
    if "{" in inner or "[" in inner or "]" in inner:
        raise YAMLSubsetError("nested flow style is not supported", lineno)
    items = []
    for part in _split_flow_items(inner):
        part = part.strip()
        if part.startswith('"'):
            v, rest = _read_dquoted(part, lineno)
            if rest.strip():
                raise YAMLSubsetError(f"unexpected content after quoted scalar: {rest!r}", lineno)
            items.append(v)
        elif part.startswith("'"):
            v, rest = _read_squoted(part, lineno)
            if rest.strip():
                raise YAMLSubsetError(f"unexpected content after quoted scalar: {rest!r}", lineno)
            items.append(v)
        else:
            items.append(_resolve_plain_scalar(part))
    return items


def _parse_scalar_or_flow_check(val_str, lineno):
    if val_str.startswith("{"):
        raise YAMLSubsetError("flow style ('{...}') is not supported", lineno)
    if val_str.startswith("["):
        return _parse_flow_sequence(val_str, lineno)
    if val_str.startswith("&"):
        raise YAMLSubsetError("anchors ('&name') are not supported", lineno)
    if val_str.startswith("*"):
        raise YAMLSubsetError("aliases ('*name') are not supported", lineno)
    if val_str.startswith("!"):
        raise YAMLSubsetError("explicit tags ('!Foo' / '!!str') are not supported", lineno)
    if val_str[:1] in ("|", ">"):
        raise YAMLSubsetError("block scalars ('|' / '>') are not supported", lineno)
    if val_str.startswith('"'):
        val, rest = _read_dquoted(val_str, lineno)
        if rest.strip():
            raise YAMLSubsetError(f"unexpected content after quoted scalar: {rest!r}", lineno)
        return val
    if val_str.startswith("'"):
        val, rest = _read_squoted(val_str, lineno)
        if rest.strip():
            raise YAMLSubsetError(f"unexpected content after quoted scalar: {rest!r}", lineno)
        return val
    return _resolve_plain_scalar(val_str)


def _split_mapping_line(content, lineno):
    if content.startswith("<<"):
        raise YAMLSubsetError("merge keys ('<<:') are not supported", lineno)
    if content.startswith('"'):
        key, rest = _read_dquoted(content, lineno)
        rest = rest.lstrip()
        if not rest.startswith(":"):
            raise YAMLSubsetError("expected ':' after quoted key", lineno)
        rest = rest[1:]
    elif content.startswith("'"):
        key, rest = _read_squoted(content, lineno)
        rest = rest.lstrip()
        if not rest.startswith(":"):
            raise YAMLSubsetError("expected ':' after quoted key", lineno)
        rest = rest[1:]
    else:
        idx = _find_colon(content)
        if idx is None:
            raise YAMLSubsetError(f"expected 'key: value' mapping entry, got {content!r}", lineno)
        key = content[:idx].strip()
        rest = content[idx + 1:]
    return key, rest.strip()


def _looks_like_mapping_entry(rest):
    if rest.startswith('"') or rest.startswith("'"):
        return True
    return _find_colon(rest) is not None


def _parse_nested_value(prepped, j, parent_indent):
    """The value following an empty 'key:' (or bare '-'): a deeper-indented block,
    a sequence aligned with the parent key/dash's own indentation (legal YAML), or
    null if neither follows."""
    if j >= len(prepped):
        return None, j
    nxt_indent = prepped[j][1]
    nxt_content = prepped[j][2]
    if nxt_indent > parent_indent:
        return _parse_block(prepped, j, nxt_indent)
    if nxt_indent == parent_indent and (nxt_content == "-" or nxt_content.startswith("- ")):
        return _parse_sequence(prepped, j, parent_indent)
    return None, j


def _parse_block(prepped, i, indent):
    if i >= len(prepped):
        return None, i
    _, line_indent, first_content = prepped[i]
    if line_indent != indent:
        raise YAMLSubsetError("unexpected indentation", prepped[i][0])
    if first_content == "-" or first_content.startswith("- "):
        return _parse_sequence(prepped, i, indent)
    return _parse_mapping(prepped, i, indent)


def _parse_sequence(prepped, i, indent):
    items = []
    while i < len(prepped) and prepped[i][1] == indent:
        lineno, ind, content = prepped[i]
        if not (content == "-" or content.startswith("- ")):
            break
        after_dash = content[1:]
        n_spaces = len(after_dash) - len(after_dash.lstrip(" "))
        rest = after_dash.lstrip(" ")
        key_col = ind + 1 + n_spaces
        if rest == "":
            i += 1
            value, i = _parse_nested_value(prepped, i, indent)
            items.append(value)
            continue
        if _looks_like_mapping_entry(rest):
            value, i = _parse_dash_mapping(prepped, i, rest, key_col)
        else:
            value = _parse_scalar_or_flow_check(rest, lineno)
            i += 1
        items.append(value)
    return items, i


def _parse_dash_mapping(prepped, i, first_rest, key_col):
    lineno = prepped[i][0]
    key, val_str = _split_mapping_line(first_rest, lineno)
    if val_str == "":
        j = i + 1
        value, j = _parse_nested_value(prepped, j, key_col)
    else:
        value = _parse_scalar_or_flow_check(val_str, lineno)
        j = i + 1
    mapping = {key: value}
    while j < len(prepped) and prepped[j][1] == key_col:
        lineno2, ind2, content2 = prepped[j]
        if content2 == "-" or content2.startswith("- "):
            break
        key2, val_str2 = _split_mapping_line(content2, lineno2)
        if val_str2 == "":
            j += 1
            value2, j = _parse_nested_value(prepped, j, key_col)
        else:
            value2 = _parse_scalar_or_flow_check(val_str2, lineno2)
            j += 1
        mapping[key2] = value2
    return mapping, j


def _parse_mapping(prepped, i, indent):
    mapping = {}
    while i < len(prepped) and prepped[i][1] == indent:
        lineno, ind, content = prepped[i]
        if content == "-" or content.startswith("- "):
            break
        if content.startswith("? ") or content == "?":
            raise YAMLSubsetError("complex keys ('? key') are not supported", lineno)
        key, val_str = _split_mapping_line(content, lineno)
        if val_str == "":
            i += 1
            value, i = _parse_nested_value(prepped, i, indent)
        else:
            value = _parse_scalar_or_flow_check(val_str, lineno)
            i += 1
        mapping[key] = value
    return mapping, i


def load_yaml_subset(text):
    lines = text.split("\n")
    prepped = []
    doc_marker_seen = False
    for idx, raw in enumerate(lines, start=1):
        leading_len = len(raw) - len(raw.lstrip(" \t"))
        leading = raw[:leading_len]
        if "\t" in leading:
            raise YAMLSubsetError("tab character used as indentation", idx)
        stripped = _strip_comment(raw)
        content = stripped.strip()
        if content == "":
            continue
        if content == "---":
            if doc_marker_seen or prepped:
                raise YAMLSubsetError("multi-document stream (second '---' document marker)", idx)
            doc_marker_seen = True
            continue
        if content == "...":
            raise YAMLSubsetError("multi-document stream ('...' end marker) is not supported", idx)
        indent = len(stripped) - len(stripped.lstrip(" "))
        prepped.append((idx, indent, content))
    if not prepped:
        return None
    value, next_i = _parse_block(prepped, 0, prepped[0][1])
    if next_i != len(prepped):
        raise YAMLSubsetError("unexpected indentation change at top level", prepped[next_i][0])
    return value


def load_instance(path):
    with open(path) as fh:
        text = fh.read()
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            die(2, f"{path}: invalid JSON: {e}")
    if ext == ".md":
        return extract_frontmatter(text)
    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml is not None:
            try:
                return yaml.safe_load(text)
            except yaml.YAMLError as e:
                die(2, f"{path}: invalid YAML: {e}")
        try:
            from ruamel.yaml import YAML
            from ruamel.yaml.error import YAMLError as RuamelYAMLError
        except ImportError:
            YAML = None
        if YAML is not None:
            from io import StringIO
            try:
                return YAML(typ="safe").load(StringIO(text))
            except RuamelYAMLError as e:
                die(2, f"{path}: invalid YAML: {e}")
        try:
            return load_yaml_subset(text)
        except YAMLSubsetError as e:
            die(2, f"{path}: line {e.line}: {e}")
    die(2, f"unsupported file extension for {path!r} (want .yaml/.yml/.json/.md)")


# --------------------------------------------------------------------------
# Fallback validator (used only when `jsonschema` is not importable).
# --------------------------------------------------------------------------
TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def resolve_ref(ref, root):
    if not ref.startswith("#/"):
        raise ValueError(f"only internal $ref supported, got {ref!r}")
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def _match(schema, inst, root):
    """Return True iff inst validates (used for oneOf/if)."""
    return not _validate(schema, inst, root, "")


def _validate(schema, inst, root, path):
    errs = []
    if isinstance(schema, bool):
        return [] if schema else [f"{path or '<root>'}: schema is false"]
    if "$ref" in schema:
        errs += _validate(resolve_ref(schema["$ref"], root), inst, root, path)
        return errs

    if "const" in schema and inst != schema["const"]:
        errs.append(f"{path or '<root>'}: must equal {schema['const']!r}, got {inst!r}")
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path or '<root>'}: {inst!r} not in {schema['enum']}")

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(TYPE_CHECKS[t](inst) for t in types):
            errs.append(f"{path or '<root>'}: expected type {schema['type']}, got {type(inst).__name__}")
            return errs  # further keyword checks assume the type held

    if isinstance(inst, str):
        if "minLength" in schema and len(inst) < schema["minLength"]:
            errs.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], inst):
            errs.append(f"{path}: {inst!r} does not match /{schema['pattern']}/")

    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append(f"{path}: {inst} < minimum {schema['minimum']}")
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append(f"{path}: {inst} > maximum {schema['maximum']}")

    if isinstance(inst, dict):
        if "required" in schema:
            for key in schema["required"]:
                if key not in inst:
                    errs.append(f"{path or '<root>'}: missing required key {key!r}")
        if "minProperties" in schema and len(inst) < schema["minProperties"]:
            errs.append(f"{path or '<root>'}: fewer than minProperties {schema['minProperties']}")
        props = schema.get("properties", {})
        for key, val in inst.items():
            child = f"{path}.{key}" if path else key
            if key in props:
                errs += _validate(props[key], val, root, child)
            elif "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                if ap is False:
                    errs.append(f"{child}: additional property not allowed")
                elif isinstance(ap, dict):
                    errs += _validate(ap, val, root, child)
            if "propertyNames" in schema:
                errs += _validate(schema["propertyNames"], key, root, f"{child} (name)")

    if isinstance(inst, list):
        if "minItems" in schema and len(inst) < schema["minItems"]:
            errs.append(f"{path or '<root>'}: fewer than minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(inst):
                errs += _validate(schema["items"], item, root, f"{path}[{i}]")

    if "oneOf" in schema:
        branch_errs = [_validate(sub, inst, root, path) for sub in schema["oneOf"]]
        matches = [i for i, be in enumerate(branch_errs) if not be]
        if len(matches) != 1:
            here = path or "<root>"
            if not matches:
                # surface the closest branch's errors so the failure is actionable
                closest = min(branch_errs, key=len)
                errs.append(f"{here}: does not match any oneOf branch; closest branch reports:")
                errs.extend(f"  {e}" for e in closest)
            else:
                errs.append(f"{here}: matched {len(matches)} oneOf branches (want exactly 1)")

    if "if" in schema and _match(schema["if"], inst, root):
        if "then" in schema:
            errs += _validate(schema["then"], inst, root, path)

    return errs


def validate(schema, inst):
    try:
        import jsonschema
        try:
            jsonschema.validate(inst, schema)
            return []
        except jsonschema.ValidationError as e:
            loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
            return [f"{loc}: {e.message}"]
    except ImportError:
        return _validate(schema, inst, schema, "")


def die(code, msg):
    print(f"validate.py: {msg}", file=sys.stderr)
    sys.exit(code)


def main(argv):
    if len(argv) < 3:
        die(2, "usage: validate.py <kind> <file> [<file> ...]")
    kind, files = argv[1], argv[2:]
    schema = load_schema(kind)
    failed = False
    for path in files:
        try:
            inst = load_instance(path)
        except FileNotFoundError:
            print(f"FAIL  {path}: no such file", file=sys.stderr)
            failed = True
            continue
        except OSError as e:
            die(2, f"{path}: {e}")
        errs = validate(schema, inst)
        if errs:
            failed = True
            print(f"FAIL  {path} ({kind}):")
            for e in errs:
                print(f"        - {e}")
        else:
            print(f"OK    {path} ({kind})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(sys.argv)
