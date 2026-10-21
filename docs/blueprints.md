# Blueprints

Ready-made automations. Click **Import**, pick your entities and rooms, save.

| Blueprint | What it does | |
|---|---|---|
| **Announce the doorbell** | Doorbell press → "Someone is at the front door" in the rooms you pick (optional urgent). | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdaredoole%2FRoomCall%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Froomcall%2Fdoorbell.yaml) |
| **Appliance finished** | Washer/dryer/dishwasher power drops after running → "The laundry is done." | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdaredoole%2FRoomCall%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Froomcall%2Fappliance_done.yaml) |
| **Do Not Disturb at bedtime** | Turns RoomCall's Do Not Disturb on at night and off in the morning. | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdaredoole%2FRoomCall%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Froomcall%2Fbedtime_dnd.yaml) |
| **Announce arrivals** | "Alex is home." when someone arrives. | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fdaredoole%2FRoomCall%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Froomcall%2Farrival.yaml) |

Each one calls [`roomcall.broadcast`](home-assistant.md#roomcallbroadcast-action), so chimes,
quiet hours, Do Not Disturb, history and notifications all apply.

## Room notify entities

Every room also has a notify entity, so anything that can send a notification can talk to a
room — automations, scripts, other integrations and the notify card:

```yaml
action: notify.send_message
target:
  entity_id: notify.roomcall_kitchen
data:
  message: Dinner is ready
```
