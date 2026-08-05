# OpenAlarm for Home Assistant

Arm, disarm and trigger your [OpenAlarm](https://openalarm.io/) alarms and panic buttons from Home Assistant automations.

OpenAlarm tells the people you choose when your alarm goes off. This integration is the bridge: your automations decide *when* something has happened, OpenAlarm handles *who finds out and in what order*.

It is a messenger, not a monitoring service. Nothing here contacts emergency services.

## What you get

Every alarm and panic button your API key can reach becomes a device, placed in an area named after its OpenAlarm location. Automations target those devices by name, so you write `Cabin Perimeter`, never a 32-character ID. Nothing else is created: no hub device, no extra entities.

Six actions:

| Action | What it does |
|-|-|
| `openalarm.alarm_arm` | Arms an alarm, optionally in a named mode |
| `openalarm.alarm_disarm` | Disarms an alarm |
| `openalarm.alarm_trigger` | Triggers an alarm, starting its escalation policy |
| `openalarm.alarm_clear` | Clears an alarm's open incident |
| `openalarm.panic_trigger` | Triggers a panic button |
| `openalarm.panic_clear` | Clears a panic button's open incident |

Your modes come from your account, including custom ones. Add a mode in the OpenAlarm console and it appears here after the next refresh, with no reinstall.

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

## Using it with Alarmo

[Alarmo](https://github.com/nielsfaber/alarmo) is the panel. OpenAlarm is the alerting layer. They fit together rather than competing:

```yaml
automation:
  - alias: "Tell OpenAlarm the alarm was triggered"
    triggers:
      - trigger: state
        entity_id: alarm_control_panel.alarmo
        to: "triggered"
    actions:
      - action: openalarm.alarm_trigger
        data:
          device_id: !input openalarm_device

  - alias: "Keep OpenAlarm in step when arming"
    triggers:
      - trigger: state
        entity_id: alarm_control_panel.alarmo
        to: "armed_away"
    actions:
      - action: openalarm.alarm_arm
        data:
          device_id: !input openalarm_device
          mode: away
```

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

## Licence

MIT. See [LICENSE](LICENSE).
