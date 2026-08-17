# Trace Replay And Agent Evaluation

Use this reference when changing `generate_trace.py`, `replay_trace.py`, execution traces, or Agent
evaluation. Template matching internals are documented in
[visual-template-matching.md](visual-template-matching.md).

## Artifact Roles

`edr.success-trace/v1` is the immutable golden path compiled from one recorded test case.
`edr.execution-trace/v1` is evidence from one replay or Agent attempt. Never overwrite the golden
trace with execution results.

The golden trace is a linked path:

```text
entry -> step-0001 -> step-0002 -> ... -> null
```

Validation rejects cycles, missing targets, unsupported actions, and unreachable nodes. A trace
with a required template missing is `incomplete` and must not run as a ready visual path.

## Replay Modes

`dom_first` waits for a visible DOM locator and falls back to the recorded template only when DOM
targeting fails before the action begins.

`visual_only` does not construct DOM locators for positional Agent actions. It locates the
pre-action template and acts at the recorded relative point. `PressKey` reuses keyboard focus only
when a successful preceding visual action targeted the same recorded selector; otherwise replay
fails rather than pressing an unproven active element.

Manual `Assert` nodes are different: they are runner-owned `verifier` nodes and use the recorded
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
| `Assert` | DOM verifier with polling and explicit expected value |

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

If nothing is pending, an intercepted click is still a failure. Interception is never itself a
reason to pass a step.

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
