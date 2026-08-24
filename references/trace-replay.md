# Trace Replay And Agent Evaluation

Use this reference when changing `generate_trace.py`, `replay_trace.py`, execution traces, or Agent
evaluation. Template matching internals are documented in
[visual-template-matching.md](visual-template-matching.md).

## Artifact Roles

`edr.success-trace/v2` is the immutable golden path compiled from one recorded test case.
`edr.execution-trace/v1` is evidence from one replay or Agent attempt. Never overwrite the golden
trace with execution results.

The golden trace is a linked path:

```text
$meta.entry -> step_0001 -> step_0002 -> ... -> []
```

Validation rejects cycles, missing targets, unsupported actions, and unreachable nodes. A trace
with a required template missing is `incomplete` and must not run as a ready visual path.

## Why v2 Looks The Way It Does

The trace feeds two runtimes: this repo's `replay_trace.py`, and maa-fw's `MaaNodeRunner`. The
latter loads a MaaFramework-style node table. v1 wrapped the nodes in an envelope
(`{schema, name, entry, steps:{...}}`) that maa-fw cannot load, even though the **node bodies** on
both sides were already nearly identical.

So v2 drops the envelope. The top level *is* the node table, and trace-level metadata moves into a
reserved `$meta` node. The layout is not invented here — it is exactly what maa-fw's
`MaaNodeRunner._coerce_node` reads, i.e. `LearnedNode.to_pipeline_node()`:

```text
<node_name>:
  recognition: {type, param}          TemplateMatch | OCR | DirectHit
  action:      {type, param}          Click | InputText | PressKey | DoNothing | ...
  next:        [node_name, ...]
  max_hit, rate_limit, timeout, pre_delay, post_delay, on_error
  attach:
    app / task_key / scene_key
    confidence_policy                 our thresholds
    gui_target                        element box, label, crop path
    verification                      network expectations, manual assertions
    provenance                        everything web-specific
    stats
```

`scripts/trace_schema.py` is the single definition of this shape. Both the generator and the tests
build nodes through `build_node` / `build_trace` there — never by writing the nested literal twice,
because a second copy of the layout is a second thing that can drift.

### Field placement rules that are not negotiable

**`attach.confidence_policy` and `attach.gui_target` are deserialised with `**`.** maa-fw does
`ConfidencePolicy(**attach["confidence_policy"])` and `GuiTarget(**attach["gui_target"])`, so one
extra key raises `TypeError` and the **whole trace fails to load**. Our own extra parameters —
`scaleFactors`, `ambiguityMargin`, `templateOrder`, the full template set — therefore live in
`attach.provenance`. `validate_trace` enforces this, because nothing on our side would otherwise
notice: our replay never touches those two dicts, so a violation is invisible here and fatal there.

**`recognition.param.template` is a single path string**, matching what maa-fw's own compiler
emits. The ordered `context -> element` fallback is our capability, so it stays in
`provenance.templates` / `provenance.templateOrder`.

**The click point travels as a ratio, not pixels.** It goes in `action.param.target_ratio`, a field
maa-fw already has for exactly this reason ("survive a rescaled match at replay time"). Using
`target_offset` would be wrong: it is a pixel offset, and the match box is scaled, so an exported
pixel value is off whenever the scale is not 1 — which is the entire point of reusing templates
across resolutions.

**Every node has a `recognition`.** Steps with no image recognition get `DirectHit`. This means
`node.get("recognition")` is **no longer** a test for "does this step have a visual fallback" —
use `trace_schema.has_template()`. Getting this wrong makes every template-less step look like it
has a visual fallback: DOM failures stop re-raising the real cause and instead run a doomed match,
reporting "visual match score too low" — the single most misleading failure this system produces.

### `$meta` must stay inert

`MaaNodeRunner.run` with no `start_nodes` queues the **entire** table, `$meta` included, and
`$meta`'s `DirectHit` recognition matches unconditionally. It is kept inert by `max_hit: 1` plus
`attach.stats.hit_count: 1`, which makes `_should_skip_node` skip it every time. It also has no
`next`, is never anyone's `next`, and is never the `entry`.

### Which assertions maa-fw can actually verify

maa-fw's SKILL.md makes verification first-class, so assertions are mapped to real nodes where
possible rather than being dropped:

| Recorded assertion | v2 node | Why |
|---|---|---|
| text / value with a static expected string | `recognition: OCR`, `param.expected: [text]` | OCR's home turf |
| existence rewritten from a text tautology | `recognition: OCR` on the anchor text | the rewrite changed the *web* wording, not the intent — and this is the majority of real recordings |
| existence with no text anchor (canvas, chart) | `DirectHit`, assertion marked `web-only` | recorder crops templates only for positional steps |
| `expectedFrom` (runtime-computed expected value) | `DirectHit`, marked `web-only` | OCR `expected` is static; freezing a recorded literal into it is a time bomb — green today, red tomorrow |
| `checked` / `attribute` | `DirectHit`, marked `web-only` | no desktop equivalent |
| HTTP status / request body expectations | kept in `attach.verification.responses`, marked `web-only` | invisible from the desktop side |

`web-only` is `trace_schema.VERIFY_SCOPE_WEB`. Marking is not decoration: an unmarked expectation
that maa-fw cannot check would be silently treated as verified.

## Replay Modes

When replay receives a `trace.json` path, it restores the case-scoped `.auth` snapshot before the
first navigation. The snapshot includes context cookies, origin-scoped localStorage, and the active
page's origin-scoped sessionStorage. Passing an already loaded trace dictionary requires an explicit
`session_state_dir`; credentials are never embedded in the golden or execution trace.

`dom_first` waits for a visible DOM locator and falls back to the recorded template only when DOM
targeting fails before the action begins.

`visual_only` does not construct DOM locators for positional Agent actions. It locates the
pre-action template and acts at the recorded relative point. `PressKey` reuses keyboard focus only
when a successful preceding visual action targeted the same recorded selector; otherwise replay
fails rather than pressing an unproven active element.

Manual assertion nodes are different: they are runner-owned `verifier` nodes and use the recorded
DOM selector in both modes. This prevents the Agent from grading its own pixels while still
allowing the Agent's actions to be fully visual.

## Reliable Action Order

For each node, replay must follow this order:

1. Register every response expectation.
2. Resolve the action target.
3. Execute exactly one action.
4. Wait for expected responses.
5. Validate status and request/response body contracts.
6. Record the execution result.

Listeners created after the action can miss fast responses. A slow response remains attached to
the action whose request timestamp falls inside that action's interval.

Never retry an action after it may have started. A click that sent a request and then timed out may
already have changed server state; visual fallback at that point would double-submit.

## Action Semantics

| Action | Replay Requirement |
|---|---|
| `Click` | one DOM or visual click |
| `DoubleClick` | one double-click operation, not two trace nodes |
| `InputText` | focus visual target when needed, then replace current text |
| `Check` / `Uncheck` | use Playwright state-aware methods in DOM mode |
| `SetSwitch` | read recorded state carrier, click only if needed, poll desired state unless gated |
| `PressKey` | locator press in DOM mode; current keyboard focus in visual mode |
| `DoNothing` | assertion node: DOM verifier with polling and explicit expected value (spec in `attach.verification.assertion`) |

An environment-backed input such as `REC_PASSWORD` fails if the variable is absent or empty. It
must never silently input an empty string and report success.

### Skipping An Optional Step Is Provisional

An `optional` step is skipped only on proven absence — but absence proven at one moment is not
absence forever. Welcome dialogs and notices often appear several seconds after load, while the step
that dismisses them sits first in the trace. The dismissal gets skipped, the overlay then appears,
and every later click is swallowed by it. What surfaces is a pile of click timeouts with no hint of
the cause.

So a skipped optional step stays pending. When a later step fails specifically because something
intercepts pointer events — "found it but cannot click it", which is a different diagnosis from
"cannot find it" — replay performs the pending optional steps and retries the current step once.
Both the recovery and the retry count as `retries`, so the efficiency score pays for them.

The dismissal step can also sit *after* the blocked one: during recording the dialog appeared later
than the click, on replay it appeared earlier. Replay may therefore pull one **overlay-dismissal**
step forward. Eligibility is not a guess — the recorder marks `dismissesOverlay` only when it
watched the click remove the layer, and only for a **textless icon** (a close X). Clicking
a dropdown option also removes its layer, and that is a real action; labelled confirm buttons are
deliberately left out too, since "确定" inside a form dialog is a submit. Missing a few dismissals
is safe; marking a real action optional is not.

Two refinements came from the real console:

- **In-flow notice bars count too.** A "please bind your phone number" banner sits in normal page
  flow with no `position: absolute` and no high `z-index`, so the floating-layer test misses it —
  yet it is just as conditional, and leaving it required breaks the trace on any account that has
  already dismissed it. Weaker container evidence demands stronger element evidence: for a notice
  the icon's own class must look like a closer (`close`/`dismiss`), which is what keeps a
  row-delete icon — same "container vanished" evidence, destructive meaning — out.
- **Watch a candidate set, not the first ancestor that looks right.** That banner lived inside an
  absolutely positioned header, so walking up hit the header first — and a header never disappears,
  so the watch timed out and the step was never marked. The recorder now watches every candidate
  layer and accepts whichever one actually vanishes, the same shape of fix as picking the switch
  layer that actually carries state. A collapse animation that flattens height to zero counts as
  vanished.

When a node is performed early, the later visit records `status: skipped` with
`performedEarly: true` rather than pretending it ran in order.

If nothing is pending, an intercepted click is still a failure. Interception is never itself a
reason to pass a step.

Selector shape is never optionality evidence. In particular, a CSS fallback can be a canvas hot
spot, custom control, or destructive icon. Only recorder-observed overlay dismissal and explicitly
gated follow-up steps become optional.

Before each web replay node, the runner checks the recorded top-level path/query/fragment. A route
mismatch fails before the action instead of clicking a coincidentally similar element on the wrong
page. Nested frame targets are resolved through the recorded outer-to-inner frame chain; `framePath`
remains a compatibility fallback for older recordings.

### Switches Behind A Confirmation Dialog

A trace that flips policy switches mutates the state it starts from, so every switch step must be
idempotent — already in the target state means do nothing. That alone is not enough when the state
change is gated behind a confirmation dialog: the class or `aria-checked` only changes after a later
click, so polling for arrival inside the switch step waits for something that cannot happen yet.

The recorder decides this from evidence rather than from a timeout guess: if any other step was
recorded between the switch click and the state change, that change needed follow-up interaction.
It marks the step `via.gated` and lists the intervening step ids in `via.gatedSteps`.

- `via.gated` — replay clicks and moves on; the following steps drive the state home.
- `via.gatedSteps` — those nodes become `optional`, because the dialog only appears when the switch
  actually needed flipping. Optional still means *skip on proven absence only*: if the dialog did
  open, the confirm click is mandatory.

Without the gate flag a gated switch degrades into a blind `Click`, which flips the switch in
whichever direction the current state implies — and reports success either way.

Text assertions follow Playwright string semantics and normalize whitespace. Value and attribute
assertions remain exact.

## Execution Trace

Each execution step records:

- golden `nodeId`, expected action, and actual action
- success or failure and duration
- target mode: `dom`, `visual`, `keyboard`, or `verifier`
- visual match, verification, scale, template kind, and click point when applicable
- validated responses
- retries and error details

The execution trace must declare `edr.execution-trace/v1` and the matching golden schema. Reject
unknown schemas instead of attempting a best-effort score.

## Evaluation

`evaluate_trace` reports:

| Metric | Meaning |
|---|---|
| `taskSuccess` | execution succeeded and golden nodes occurred in exact path order |
| `stepCompletionRate` | successful golden nodes divided by golden node count |
| `actionAccuracy` | expected and actual action types agree |
| `networkAssertionRate` | validated expected responses, capped per golden node |
| `trajectoryOrderRate` | golden nodes occurred in the recorded order |
| `trajectoryEfficiency` | penalty for extra actions and retries |
| `extraActionCount` | execution steps beyond golden path length |
| `retryCount` | total retries reported by execution |
| `averageVisualMatchScore` | mean template score for visual targets |

A correct, direct replay scores 100. Extra responses cannot inflate network score. Reversed steps
cannot satisfy `taskSuccess`. Extra actions and retries reduce efficiency and total score even when
the final business state succeeds.

## Usage

Both `assets/` and `scripts/` must be importable:

```python
from replay_trace import evaluate_trace, load_trace, replay_trace

case_dir = "recordings/flow"
golden = load_trace(f"{case_dir}/trace.json")
execution = replay_trace(
    page,
    golden,
    template_root=case_dir,
    targeting="visual_only",
    timeout_ms=5000,
    env={"REC_PASSWORD": "..."},
    execution_path=f"{case_dir}/execution.json",
)
report = evaluate_trace(golden, execution)
```

Use `raise_on_error=True` when the caller wants pytest-style immediate failure. Leave it false when
the execution trace itself is the evaluation artifact.
