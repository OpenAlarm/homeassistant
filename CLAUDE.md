# OpenAlarm - Home Assistant integration

The official [OpenAlarm](https://openalarm.io/) integration for Home Assistant,
distributed through HACS. This repository is public: the code, the commit
history, and this file are all part of how OpenAlarm presents itself to the
Home Assistant community. Write everything here to that standard.

## What this is, and deliberately is not

OpenAlarm is the **alerting layer**. When an alarm fires, OpenAlarm decides who
gets told, in what order, until someone acknowledges. This integration exposes
that to Home Assistant - it is **not an alarm system** and must never grow into
one:

- **No arming engine.** No sensor configuration, no entry/exit delay logic, no
  bypass. Panels like [Alarmo](https://github.com/nielsfaber/alarmo) do that
  excellently; OpenAlarm sits underneath them. Shipping arming logic would move
  this project into the alarm-system comparison frame, which is the one frame
  it should never occupy. This is a closed decision - do not reopen it in
  response to feature requests; point them at Alarmo and the blueprint instead.
- The **panel entity is a courtesy control** for users with no panel
  integration at all, and the anchor for voice assistants. It reflects the
  service's real state; it does not decide anything.

## Layout

```
custom_components/openalarm/
  api.py                 thin client; every call carries traceId at debug level
  config_flow.py         key -> validate via describe -> pick location; reauth
  coordinator.py         inventory (describe, 6h) and state (60s) coordinators
  __init__.py            actions, device sync, dynamic mode dropdown options
  alarm_control_panel.py one panel entity per alarm, real state
  services.yaml          static action definitions (options patched at runtime)
  strings.json           source of truth; translations/en.json is a copy of it
  brand/                 icons HACS serves before the brands repo listing
tests/                   pytest, via pytest-homeassistant-custom-component
```

## Conventions

- **Versioning is semver in `manifest.json`, bumped on every user-visible
  change**, because installs track the default branch until releases exist and
  the manifest version is the only way to know what is running. **Minor** for a
  new capability a user can see or configure (a new action, entity, option, or
  a behaviour they could not get before); **patch** for anything that changes how
  the same capability behaves - a fix, a cadence, a threshold, a guard. The
  state-poll slowdown was a patch (1.8.2), not a minor: nothing new to use.
- **Action ids are noun-first** (`alarm_arm`, `panic_clear`) so they group;
  **labels are verb-first sentence case** ("Arm alarm") because that is Home
  Assistant core's register. Both were checked against core, not assumed.
- **Minimum HA is 2025.3** - `AddConfigEntryEntitiesCallback` does not exist
  before it. Raise the floor rather than importing around it.
- **`strings.json` and `translations/en.json` must stay identical** - edit
  strings.json and copy it over.
- **Verify against core source, not memory.** Every HA API claim in this repo's
  history that was written from memory was wrong at least once; the ones
  checked against `home-assistant/core` at the pinned floor were not.
- Ids from the API are opaque 32-character strings. Never parse them, never
  display them where a configured name exists.

## Commands

```bash
python3 -m venv venv && venv/bin/pip install -r requirements_test.txt
venv/bin/pytest tests -q
```

CI runs the same tests plus `hacs/action` and hassfest on every push. All three
must be green before anything merges or releases.

## House rules

- No inline code comments beyond docstrings that explain *why*.
- No em or en dashes anywhere - use a plain hyphen.
- Commits are authored solely by the repository owner; no AI co-author
  trailers or generated-with footers.
- Commit messages explain why, not what.
