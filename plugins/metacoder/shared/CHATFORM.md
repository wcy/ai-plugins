# Chat-Form Emission Convention

An **optional** interaction convention. When loaded into context at Claude Code start, it
tells Claude to emit structured `<chat-form>` XML blocks for decision questions whose answers
fit a small, fixed option set — instead of numbered prose lists. Clients that can't render the
forms degrade gracefully to the raw markdown.

Most useful during interactive brainstorming and clarification, but applies to any question
with a known, bounded option set.

## Rules

- Emit at most **one** `<chat-form>` per message, per decision batch; place it after any short
  markdown prose introducing the questions.
- Use a form only when the option set is known and 2–8 wide. Fall back to plain prose when
  options are unknown, open-ended, or >8.
- Pick `type` to match the answer UX:
  - `buttons` — 2–4 mutually exclusive options the user taps in a single row.
  - `radio` — 5+ single-select options where a wrapped button row would be ugly.
  - `checkbox` — multi-select questions where several answers may apply.
- Mark the recommended answer with `selected="true"` so the user can one-click accept the
  default. For `buttons`/`radio`, only one option carries `selected="true"`; for `checkbox`,
  any subset may be pre-checked.
- Add `<custom placeholder="…"/>` to any question where an "Other" free-text answer is reasonable.
- Every question must still read sensibly as prose — free-form prose replies remain valid at
  every prompt. Handle a plain-text answer identically to a serialized form reply, and proceed
  only after the user has answered (by submitting the form or replying in prose).

## Examples

Buttons (2–4 mutually exclusive options, with a recommendation and an "Other" fallback):

```xml
<chat-form title="Storage backend?">
  <question id="storage" type="buttons" label="Primary storage backend?" required="true">
    <option value="postgres" selected="true">PostgreSQL (recommended)</option>
    <option value="sqlite">SQLite</option>
    <option value="mysql">MySQL</option>
    <custom placeholder="Other backend"/>
  </question>
</chat-form>
```

Radio (5+ single-select options, with a recommendation):

```xml
<chat-form title="Deployment target?">
  <question id="deploy" type="radio" label="Where will this deploy?" required="true">
    <option value="kubernetes" selected="true">Kubernetes (recommended)</option>
    <option value="ecs">AWS ECS</option>
    <option value="cloud-run">Google Cloud Run</option>
    <option value="lambda">AWS Lambda</option>
    <option value="bare-metal">Bare metal / VM</option>
  </question>
</chat-form>
```

Checkbox (multi-select, with pre-checked recommendations and a custom fallback):

```xml
<chat-form title="Target environments?">
  <question id="envs" type="checkbox" label="Which environments?">
    <option value="dev" selected="true">Dev</option>
    <option value="staging">Staging</option>
    <option value="prod" selected="true">Prod</option>
    <custom placeholder="Additional environment"/>
  </question>
</chat-form>
```
