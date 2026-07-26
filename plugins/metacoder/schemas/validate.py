#!/usr/bin/env python3
"""Validate a metacoder artifact against its JSON Schema.

Usage:
    python3 ${CLAUDE_PLUGIN_ROOT}/schemas/validate.py <kind> <file> [<file> ...]

<kind> is the schema basename (with or without the .schema.json suffix), one of:
    catalog | change-frontmatter | plan-graph | project-state | plan-state
    conformance-report | story-report | inconsistency-report

Input file type is auto-detected:
    *.yaml / *.yml   -> parsed as YAML  (needs PyYAML)
    *.json           -> parsed as JSON
    *.md             -> HTML-comment front-matter (<!-- key: value -->) is extracted
                        into an object (used for change docs)

Exit codes: 0 = all valid, 1 = a validation error, 2 = a usage / load error.

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


def load_instance(path):
    with open(path) as fh:
        text = fh.read()
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return json.loads(text)
    if ext == ".md":
        return extract_frontmatter(text)
    if ext in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            try:
                from ruamel.yaml import YAML
                from io import StringIO
                return YAML(typ="safe").load(StringIO(text))
            except ImportError:
                die(2, "PyYAML (or ruamel.yaml) is required to validate YAML files: "
                       "pip install pyyaml")
        return yaml.safe_load(text)
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
