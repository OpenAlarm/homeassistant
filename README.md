# OpenAlarm for Home Assistant

Arm, disarm and trigger your [OpenAlarm](https://openalarm.io/) alarms and panic buttons from Home Assistant automations.

OpenAlarm tells the people you choose when your alarm goes off. This integration is the bridge: your automations decide *when* something has happened, OpenAlarm handles *who finds out and in what order*.

It is a messenger, not a monitoring service. Nothing here contacts emergency services.

## What you get

Every alarm and panic button your API key can reach becomes a device. Automations target those devices by name, so you write `Cabin Perimeter`, never a 32-character ID. Nothing else is created: no hub device, no extra entities.

State stays in step in both directions. The integration holds a push connection to OpenAlarm, so an arm, disarm or trigger made anywhere - the console, another integration, a curl - shows up on the panel within a couple of seconds. If the connection drops, the integration falls back to its one-minute poll until it reconnects.

Six actions:

| Action | What it does |
|-|-|
| `openalarm.alarm_arm` | Arms an alarm, optionally in a named mode |
| `openalarm.alarm_disarm` | Disarms an alarm |
| `openalarm.alarm_trigger` | Triggers an alarm, starting its escalation policy |
| `openalarm.alarm_clear` | Clears an alarm's open incident |
| `openalarm.panic_trigger` | Triggers a panic button |
| `openalarm.panic_clear` | Clears a panic button's open incident |

Your modes come from your account, including custom ones - and they show up in the arm and trigger dropdowns by their configured names, not their ids. Add or rename a mode in the OpenAlarm console and the dropdown follows on the next refresh or reload, with no reinstall.

Each alarm also carries an **alarm control panel entity**, so dashboards and panel cards can arm, disarm and trigger it like any other alarm. Its state is the service's real state, polled every minute: arm it from anywhere else - a script, another integration, a curl - and the entity follows within that minute, and an open incident shows as triggered until it clears. (The console never arms anything, by design: it reads state, and arming arrives only through the API.) Commands reflect immediately. Custom modes are armed through `openalarm.alarm_arm` and show on the panel as armed custom; the panel's own buttons cover Home, Away and Night.

## Requirements

- Home Assistant 2025.3 or newer
- An OpenAlarm account and an API key

## Installation

### HACS

1. HACS → Integrations → search for **OpenAlarm** → Download.
2. Restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → **OpenAlarm**.

Until this repository is in the HACS default list, add it as a custom repository: HACS → three-dot menu → Custom repositories → `https://github.com/OpenAlarm/homeassistant`, category **Integration**.

### Manual

Copy `custom_components/openalarm` into your Home Assistant `config/custom_components/` directory and restart.

## Setup

Create an API key in the OpenAlarm console under **API Keys**, then paste it into the config flow.

The key decides what Home Assistant can see. A key scoped to one alarm exposes one alarm. Scope it deliberately: this key lives in your Home Assistant configuration, and anything it can reach, an automation can fire.

If the key reaches more than one location, you will be asked which one to set up. Locations are configured separately, so add the integration again for each.

## Using it with Alarmo (or any alarm panel)

[Alarmo](https://github.com/nielsfaber/alarmo) is the panel. OpenAlarm is the alerting layer. They fit together rather than competing, and the blueprint wires them in one step:

[![Import blueprint into Home Assistant](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FOpenAlarm%2Fhomeassistant%2Fmain%2Fblueprints%2Fautomation%2Fopenalarm%2Fforward_alarm_panel.yaml)

Pick your panel entity and your OpenAlarm alarm, and the automation keeps them in step: arming states mirror across, a trigger opens an OpenAlarm incident so your contacts are alerted, and a disarm ends it. It works with any `alarm_control_panel` entity - Alarmo, a Ring keypad, the manual panel, anything.

Prefer to write it yourself? The blueprint is plain YAML at
[`blueprints/automation/openalarm/forward_alarm_panel.yaml`](blueprints/automation/openalarm/forward_alarm_panel.yaml) - the same service calls work in any automation.

## Refreshing

The integration re-reads your inventory every six hours. To pick up a console change immediately, reload the entry: Settings → Devices & Services → OpenAlarm → three-dot menu → **Reload**.

A trigger you delete, disable, or drop from the key's scope stops appearing, and its device is detached rather than left behind offering a control that would fail.

## Removing it

Settings → Devices & Services → OpenAlarm → three-dot menu → Delete. That removes the entry, its devices and its entities. Nothing is left in your OpenAlarm account, and the API key stays valid until you delete it in the console.

## Troubleshooting

Every call carries a trace ID. Turn on debug logging and quote it in a support request:

```yaml
logger:
  default: warning
  logs:
    custom_components.openalarm: debug
```

**"OpenAlarm rejected that key"** - the key was deleted or disabled in the console. Issue a new one; Home Assistant will prompt you to reconnect.

**An action fails with "OpenAlarm no longer has ..."** - that trigger was deleted, disabled, or dropped from the key's scope.

## Development

```bash
python3 -m venv venv && venv/bin/pip install -r requirements_test.txt
venv/bin/pytest tests -q
```

CI runs the same tests plus HACS validation and hassfest on every push.

## Licence

MIT. See [LICENSE](LICENSE).
