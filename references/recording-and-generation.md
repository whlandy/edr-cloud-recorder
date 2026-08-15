# Recording And Generation

Use this reference for recorder configuration, generated artifacts, and regeneration from old raw
recordings. Selector design belongs in [selectors.md](selectors.md); authentication belongs in
[auth-and-session.md](auth-and-session.md).

## Configuration

The recorder accepts command-line arguments and `config.json`. Resolution order is:

1. `--config <path>`
2. project-local `config.json`
3. `~/.config/edr-cloud-recorder/config.json`

```json
{
  "baseUrl": "https://app.example.com",
  "entryPath": "/index.html#/home",
  "auth": {"user": "alice", "password": ""},
  "record": {"apiFilter": "/api/", "outDir": "recordings"}
}
```

Command-line values override configuration. `REC_USER` and `REC_PASSWORD` override `auth`.
Prefer environment variables for passwords. If a password is stored in the user-level file, set
its permissions to `0600`.

Common options:

```text
--url <url>       Recording start URL
--name <flow>     Artifact base name
--api <fragment>  Network URL filter
--out <directory> Output directory
--headless        Smoke checks only, not manual recording
```

## Artifact Contract

One recording creates one raw capture, one pytest draft, one golden trace, and an optional template
directory. A trace represents the whole test case, not one trace per click.

```text
<flow>.json
test_<flow>.py
<flow>.trace.json
<flow>.assets/step-*.element.png
<flow>.assets/step-*.context.png
```

The raw JSON is the source of truth. Keep original request and response bodies there; volatility is
handled during generation, not recording.

## Generation Guarantees

The pytest generator:

- removes only the leading login segment and uses `authed_page`
- starts with `dismiss_overlays(page)`
- corrects adjacent `press` then `fill` event-order inversions
- converts write requests into pre-armed request waits plus status/body assertions
- keeps GET traffic as comments by default
- widens volatile strings and large timestamp-like numbers without dropping structure
- preserves unsupported selectors as visible warnings instead of aborting the whole draft
- rejects a `start_url` origin that does not match any recorded absolute URL

Network calls belong to the action that started the request. Use request timestamps when available;
a slow response may finish after the next UI action and must not be reassigned.

## Manual Assertions

Right-click recording supports:

| Recorded Assertion | Pytest Meaning |
|---|---|
| `text` | exact text with Playwright whitespace normalization |
| `value` | input value |
| `visible` | visible or hidden, based on boolean expected value |
| `checked` | checked or unchecked |
| `attribute` | named attribute value |

Empty strings require explicit confirmation. Boolean assertions always store an explicit expected
value, including `false`.

## Regenerating An Old Recording

Use the `startUrl` stored in the raw recording:

```python
import json
import sys

sys.path.insert(0, "<skill>/scripts")
from generate_spec import _ident, generate_spec
from generate_trace import generate_trace

data = json.load(open("recordings/old.json", encoding="utf-8"))
name = "old"
spec = generate_spec(
    data["steps"], data["net"], start_url=data["startUrl"], name=name
)
trace = generate_trace(
    data["steps"], data["net"], start_url=data["startUrl"], name=name
)
open(f"recordings/test_{_ident(name)}.py", "w", encoding="utf-8").write(spec)
open(f"recordings/{name}.trace.json", "w", encoding="utf-8").write(
    json.dumps(trace, ensure_ascii=False, indent=2)
)
```

Regeneration can improve code structure, network matching, assertions, and trace compilation. It
cannot recalculate selectors or visual templates because the live DOM and pre-action pixels no
longer exist. Re-record when those inputs are stale or missing.
