---
name: web-record
description: >-
  Record a person's real browser workflow as replayable Playwright Python tests, correlate UI
  actions with HTTP requests and responses, generate visual-template success traces, replay them,
  and evaluate an agent against the recorded path. Use for web E2E recording, regression flows,
  UI-to-API investigation, visual target replay, and cloud-to-endpoint verification. Do not use
  for pure API tests, mobile apps, or desktop-only workflows.
---

# Web Record

Turn one real browser session into three related artifacts:

- raw evidence: steps, network events, timestamps, and rendered UI templates
- a maintainable Playwright Python test
- a replayable golden trace for visual execution and Agent evaluation

The host language is Python. `scripts/recorder-inject.mjs` is JavaScript only because it executes
inside the browser page; do not rewrite it into Python.

## Non-Negotiable Rules

1. The user performs the recorded workflow. Do not click through it for them.
2. Establish network listeners before the action that triggers them.
3. Never use `wait_for_timeout` or `networkidle` to hide synchronization problems.
4. A green run is not enough. Check assertions, skipped steps, cleanup, and repeatability.
5. Real writes require explicit user intent and cleanup in `try/finally`.
6. Never weaken an assertion merely to make replay pass.

## Core Workflow

### 1. Prepare Once

Run in the target project or an empty working directory:

```bash
cp -r <skill>/assets/* .
python -m pip install -r requirements.txt
python -m playwright install chromium
```

The final command can be skipped when `chrome_path.py` finds a compatible local Chromium.

### 2. Record

Give the command to the user, then wait for them to close the browser:

```bash
python <skill>/scripts/record.py --url https://app.example.com --name <flow>
```

Do not add `--headless` for manual recording. It exists for smoke tests.

The recording produces:

| Artifact | Purpose |
|---|---|
| `recordings/<flow>/recording.json` | Raw steps, network events, and UI metadata |
| `recordings/<flow>/assets/*.png` | Pre-action element and context templates |
| `recordings/<flow>/test_<flow>.py` | Playwright pytest draft |
| `recordings/<flow>/trace.json` | One complete golden path, in the MaaFramework node-table shape
  (`edr.success-trace/v2`) that maa-fw's `MaaNodeRunner` loads directly |

Configuration, regeneration, and artifact rules are in
[recording-and-generation.md](references/recording-and-generation.md).

### 3. Add Intent While Recording

Right-click an element to add a manual assertion. Supported checks are text, input value,
visibility, checked state, and an attribute value. The user must confirm the expected value;
the recorder must not infer intent from the current value without confirmation.

In visual replay, actions may use templates, but manual assertions are independent DOM-backed
`verifier` nodes. This keeps evaluation separate from the Agent's targeting mechanism.

Read [ui-assertions.md](references/ui-assertions.md) when choosing assertion points or debugging
an assertion that appears to read the wrong object.

### 4. Replay the Pytest Draft

```bash
pytest recordings/<flow>/test_<flow>.py
```

Review the draft before accepting it. Remove accidental actions, resolve any `AMBIGUOUS` marker,
and add cleanup for writes.

### 5. Replay and Evaluate the Golden Trace

Use `dom_first` for resilient E2E replay or `visual_only` to evaluate image-based targeting:

```python
from replay_trace import evaluate_trace, load_trace, replay_trace

case_dir = "recordings/<flow>"
golden = load_trace(f"{case_dir}/trace.json")
execution = replay_trace(
    authed_page,
    golden,
    template_root=case_dir,
    targeting="visual_only",
    execution_path=f"{case_dir}/execution.json",
)
report = evaluate_trace(golden, execution)
assert report["taskSuccess"]
```

Read [trace-replay.md](references/trace-replay.md) before changing the trace schema, runner,
network expectations, or evaluation metrics. Read
[visual-template-matching.md](references/visual-template-matching.md) before changing template
capture or matching.

### 6. Audit What the Green Actually Proves

```bash
python3 ../trace-eval/trust/audit.py recordings/<flow>
```

A trace can replay at score 100 and still prove nothing — the recorded corpus had
5 of 7 traces with **zero assertions**, and every assertion that did exist was a
tautology (`get_by_text("X")` then assert its text is `"X"`). Replay evaluation answers
"did this run go through"; the audit answers "is this trace worth trusting", along two
separate axes: whether it will replay the same way again, and what a green run proves.

The audit reports evidence per node, not just a number. Its score expresses **ordering
only** — it is not calibrated to a probability, and it says so in every report.

It lives in a separate project, [trace-eval](../trace-eval), because the two have
opposite jobs: this repository produces traces, that one distrusts them. It reads the trace
shape from `scripts/trace_schema.py` here rather than copying it — a copy would drift, and a
drifted copy would judge traces against a shape nothing actually produces.

### 7. Verify the Green Result

```bash
grep -rn "skip\|xfail" tests/ recordings/
grep -n "assert \|assert_subset" recordings/<flow>/test_<flow>.py
pytest recordings/<flow>/test_<flow>.py && pytest recordings/<flow>/test_<flow>.py
```

For stateful workflows, also retry with a fresh browser profile or cleared session storage.
For write operations, confirm cleanup executes after success, assertion failure, and interruption.

## Failure Triage

| Symptom | Suspect First | Check |
|---|---|---|
| request/response timeout | the action did not land | run the action without the listener wrapper |
| strict-mode violation | selector collision | scope to the nearest stable unique ancestor |
| element missing | volatile scope or collapsed parent | inspect dates, IDs, and parent state |
| click succeeds, UI does nothing | wrong container or already-selected state | inspect the semantic child and state carrier |
| click intercepted | visible overlay | close visible overlays; do not delete DOM nodes |
| switch ends in wrong state | blind click or wrong state carrier | inspect recorded `via` and poll the target state |
| trace passes with suspicious behavior | weak verifier or missing path checks | inspect execution trace, assertions, extras, retries |

More environment and browser failures are covered by
[troubleshooting.md](references/troubleshooting.md).

## Reference Router

Load only the reference needed for the current task:

| Reference | Read When |
|---|---|
| [recording-and-generation.md](references/recording-and-generation.md) | configuring recording, understanding artifacts, regenerating old captures |
| [trace-replay.md](references/trace-replay.md) | replaying traces, execution schema, Agent scoring, replay failures |
| [visual-template-matching.md](references/visual-template-matching.md) | template capture, scale handling, ambiguity, visual safety |
| [selectors.md](references/selectors.md) | selectors collide, drift, or hit the wrong element |
| [auth-and-session.md](references/auth-and-session.md) | login reuse, SSO, sessionStorage, profile isolation |
| [ui-assertions.md](references/ui-assertions.md) | choosing or debugging manual UI assertions |
| [safe-writes.md](references/safe-writes.md) | a test changes server-side data or must capture without sending |
| [endpoint-orchestration.md](references/endpoint-orchestration.md) | cloud changes must be verified through edr-wd on an endpoint |
| [troubleshooting.md](references/troubleshooting.md) | browser startup, certificates, missing traffic, intermittent failures |

## Repository Map

```text
SKILL.md                    Core workflow and reference router
agents/openai.yaml          Codex-facing skill metadata
scripts/record.py           Browser recording driver
scripts/recorder-inject.mjs In-page recorder
scripts/generate_spec.py    Pytest generator
scripts/generate_trace.py   Golden trace compiler
scripts/trace_schema.py     The one definition of the trace shape
scripts/replay_trace.py     Trace runner and evaluator
assets/                     Runtime files copied into target projects
orchestrate/                Cloud and endpoint coordination
references/                 Details loaded only when needed
test/                       Recorder, generator, replay, and structure checks
```

## Self-Check

After changing recording, generation, visual matching, or trace replay:

```bash
pytest -q
python scripts/check_visual.py
```

Do not claim replay success if only syntax or generated strings were checked. The relevant path
must execute, and tests must include both a success case and the failure mode being guarded.
