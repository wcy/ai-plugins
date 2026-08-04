# ai-plugins

Claude Code plugins published by [William Yap](https://github.com/wcy).

## Install

**Prerequisite.** `metacoder` needs two Python packages at runtime — install them first:

```
pip install pyyaml jsonschema
```

This is the plugin's only setup step and it is **never** performed on your behalf: the plugin never
installs packages into your environment, ships **no hooks**, and runs nothing at session start.
Skipping it leaves every skill failing fast with a named `E_MISSING_PREREQ` diagnostic naming this
command, rather than misbehaving.

Then register this repo as a plugin marketplace and install whichever plugin you want:

```
/plugin marketplace add wcy/ai-plugins
/plugin install metacoder@ai-plugins
```

Installing `metacoder` registers all nine skills — `mspec`, `mreverse`, `mplan`, `mverify`,
`mexecute`, `mquick`, `mreq`, `mfix`, and `mmigrate` — and makes them invokable. Nothing is copied
into the target project's `.claude/`.

## Plugins

| Plugin | Description |
|---|---|
| [metacoder](plugins/metacoder/README.md) | Spec-to-ship workflow that keeps codebases coherent: write specs (mspec), reverse-engineer/reconcile specs from code (mreverse), plan implementations (mplan), verify conformance (mverify), fix the drift it finds by deciding code-vs-spec (mfix), execute plans as worktree-isolated waves (mexecute), run the whole loop autonomously (mquick), author or derive the requirements layer (mreq) that specs build from, and keep every tracked artifact's own schema, identifiers, and cross-references conformant (mmigrate) |

## License

MIT — see [LICENSE](LICENSE).
