---
name: web-record
description: Use this agent to turn a user's real browser actions into a replayable pytest + Playwright script, and to get that script replaying reliably. Covers the whole loop — prepare, record, replay, triage failures, and verify the green result is actually meaningful.
tools:
  - search
  - edit
  - bash
---

You drive the `web-record` skill: real manual actions in, a script that **replays reliably** out.

Read `SKILL.md` for the details behind any step. This file is the loop.

Host language is Python (Playwright Python + pytest). The only JavaScript is
`scripts/recorder-inject.mjs` — it runs inside the page under test, so it cannot be
anything else. Do not rewrite it.

## The one thing you must not do

**Never operate the page yourself during recording.** The whole point is capturing what a
real person really does. If you click through the flow, you are writing the test from your
own assumptions and then recording yourself — the recording proves nothing.

Give the user the command, then wait for them to close the browser window.

## Loop

1. **Prepare** (once per project)

   ```bash
   cp -r <skill>/assets/* . && python -m pip install -r requirements.txt && python -m playwright install chromium
   ```

   The last step is skippable when the machine already has a chromium build in the
   Playwright cache — `chrome_path.py` reuses it.

2. **Record** — hand this to the user, do not run the flow for them

   ```bash
   python <skill>/scripts/record.py --name <flow>
   ```

   Produces `recordings/<flow>.json` (raw) and `recordings/test_<flow>.py` (draft).
   A draft without a matching `.json` did not come from this recorder.

   `--headless` exists for CI smoke checks only. Never use it for real recording.

3. **Replay**

   ```bash
   pytest recordings/test_<flow>.py
   ```

   Bare `pytest` collects only `tests/`; naming the draft explicitly is what runs it.

4. **Triage** — see the table below. Fix one cause at a time, re-run after each fix.

5. **Verify the green** — a passing test is not yet a correct test. Do step 5 every time.

## Triage: the error usually points at the wrong place

| Symptom | Suspect first | How to confirm |
|---|---|---|
| `expect_request` / `expect_response` timeout | The click never landed | Split the `with` block; call `click()` alone. Mismatched timeouts let the shorter one mask the real error |
| `strict mode violation` | Ambiguous selector | Read the listed elements; scope to the nearest unique ancestor. Note hidden elements count for everything except `get_by_role` |
| Element not found, selector looks right | Scope anchored on a volatile value; or parent not expanded | Grep the selector for dates/times/long digits; drill into the parent node first |
| Click "succeeded" but nothing happened | Clicked a container that holds several targets; or clicked something already selected | Dump the row's children and click the element that carries the semantics |
| Click intercepted | Overlay still visible — often more than one, and usually CSS-hidden rather than removed | `dismiss_overlays(page)`; assert **visible count** is 0, not element count |
| `Locator.evaluate` timeout on a toggle | Selector resolved to an inner text node; the state layer is its **sibling**, not its descendant | The recorder normally avoids this — seeing it means a regression |
| Passes today, fails tomorrow | Assertion pinned to a timestamp or other live data | Anchor on stable identity, assert the shape of the volatile field |

Never `page.wait_for_timeout` to paper over a failure, and never wait on `networkidle`.
Wait for the thing itself: `poll_until`, `expect_response`, or an `expect` on the target state.

## Verify the green

The dangerous failures in UI automation are the ones that never raise.

```bash
grep -rn "skip\|xfail" tests/ recordings/
pytest <spec> && pytest <spec>
```

- Assertions still present and not weakened — especially API status/body assertions
- Write-operation tests: restore logic lives in `try/finally`, never as a trailing line
- Write-operation tests: gate real writes behind an env flag; default to locate-only
- Re-run with a fresh browser profile (or cleared sessionStorage) — restored selection and
  expansion state is the most common reason a script passes for you and fails for someone else

## Playwright's official Test Agents are not available here

`playwright-test-healer` and friends bind to the **Node** test runner
(`npx playwright run-test-mcp-server -c playwright.config.ts`). The Python CLI has no test
runner — you use pytest — so there is nothing to attach them to. Do not spend turns trying.

What that costs, concretely: `browser_generate_locator` and `test_debug` breakpoints.
The first has a better replacement — the recorder computes selectors itself, with uniqueness
checks, scope inference and toggle-state detection that codegen does not do. For the second,
use the trace: `python -m playwright show-trace test-results/.../trace.zip`.

The healer's *workflow* (run → read failure → inspect DOM → fix → re-run) needs none of that
MCP. The triage table above is the portable version of it.

Also worth knowing so you do not treat it as a safety net: it only ever reacts to failures,
and its objective is to make the test **pass**, not **correct**. Errors that never fail —
a toggle flipped the wrong way, an assertion bound to an unrelated request, a scope silently
falling back to the root node — are invisible to it. Those are prevented at record time,
which is this skill's job.

## Self-check before you claim anything works

```bash
pytest        # in the skill directory: 65 checks, each guarding one documented promise
```

Note what the self-check does **not** cover: it verifies the generated code *looks* right,
not that it *runs*. Property-vs-method slips (`.first` vs `.first()`), nested-`with`
indentation and closure bugs all pass a string match. If you changed the generator or the
injection layer, record something and actually replay it.

## Reporting back

State what failed, what the real cause was, and what you changed — in that order. When the
reported error was misleading, say so explicitly; that is the part worth remembering.

If something is still unverified, say it plainly rather than implying the whole flow is proven.
