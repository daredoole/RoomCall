# Siri and room announcements

Use **“Hey Siri, run RoomCall Basement”** to dictate a message to your basement
speakers. RoomCall uses the configured HA speech provider (for example Piper),
with the configured chime before speech. HomePods are not required.

Avoid naming the shortcut **Intercom**: Siri may interpret that as Apple's
HomePod Intercom command instead of running RoomCall.

## Recommended: Home Assistant Perform Action

Open the RoomCall card's **Settings → Siri Shortcuts**, choose your destination,
and leave **Perform Action — recommended setup** selected. The instructions use
the exact room ID from your installation.

![Room-specific Siri setup](assets/ha-siri-shortcuts.png)

On your iPhone:

1. Open **Shortcuts → +** and add **Ask for Input**, with input type **Text** and
   a prompt such as “What should I announce in Basement?” Siri speaks this prompt;
   opening the shortcut manually gives you a text field.
2. Add **Dictionary**, with two entries:

    | Key | Type | Value |
    |---|---|---|
    | `areas` | Text | `basement` (use the ID shown by RoomCall) |
    | `message` | Text | Select the **Ask for Input** magic variable. |

    RoomCall accepts a single room ID as well as a list. A Dictionary correctly
    handles quotes, emoji and newlines in the message.
3. Add **Home Assistant → Perform Action** (*Call Service* in older app versions).
   Select your **Server**, choose **`roomcall.broadcast`**, and set **Action data**
   to the **Dictionary** variable. Do not type the variable's name as literal text.
4. Name the shortcut **RoomCall Basement**. Run it once to grant any requested
   iOS permissions, then say **“Hey Siri, run RoomCall Basement.”**

This follows the [Companion app's supported App Intent setup](https://companion.home-assistant.io/docs/integrations/siri-shortcuts/).
It avoids the URL handler's confirmation on every call. iOS can still require
permissions, unlocking, or phone interaction depending on the device and context.
The server is selected on your phone; RoomCall cannot infer the Companion app's
private server identifier from HA.

To upgrade a URL shortcut, keep **Ask for Input**, remove **URL Encode / Open URL**,
and add **Dictionary → Perform Action** as above. Do not use Apple's Home
**Intercom** action.

## Downloadable shortcut generator

The same RoomCall settings panel also generates a file for each room or
**Everywhere**. These portable exports use the
[HA URL handler](https://companion.home-assistant.io/docs/integrations/url-handler/).
They use the first server configured in your iPhone's HA app, open the app, and
require confirmation on every run. This adds delay; use Perform Action above
when you want to avoid it.

Apple requires shared `.shortcut` files to be signed before importing on iPhone.
RoomCall offers these export options:

| Format | What you do next |
|---|---|
| **Cherri source** | Compile/sign on Linux or Mac using [Cherri](https://github.com/electrikmilk/cherri). |
| **Unsigned shortcut** | Sign it with Apple's `shortcuts` command on a Mac. |
| **Signed iPhone shortcut** | Supply your own [HubSign account/API key](https://routinehub.co/) and explicitly allow external signing. Download and open on iPhone. |

For Cherri, run this with the generated filename:

```bash
cherri "RoomCall Basement.cherri" --hubsign --share=anyone
```

This uses Cherri's external signing provider. Review that tool's current service
terms and privacy information before using it. For local signing on a Mac:

```bash
shortcuts sign --mode anyone --input "RoomCall Basement unsigned.shortcut" --output "RoomCall Basement.shortcut"
```

RoomCall's optional signing request sends the room name/ID and generated shortcut
to RoutineHub. It contains no HA address, HA token, or dictated message. Your
HubSign API key is sent to that provider, used only for this request, and never
stored by RoomCall. The password field is cleared after the request or when the
settings panel closes. Source exports require no account or external request.
Unsigned source files cannot be imported directly on an iPhone.

## Chimes and latency

RoomCall prepares one audio file containing the configured chime followed by the
speech, then sends that same clip to the selected speakers. Chime TTS is not
required for RoomCall announcements. Turning the chime off retains HA's direct
TTS media route. Invalid speech/chime preparation returns an error and sends no
partial announcement.

Delay includes Siri dictation, HA connection, speech synthesis, audio preparation,
and the speaker's startup time. The URL route adds an app switch and confirmation.
`communicate` → `diagnose` includes the local `tts_prepare` timing; delivery evidence
separates request acceptance, hosted audio fetches and matching playback reports.
None of these proves that a person heard the announcement.

## Urgent calls and people

Add `priority` → Text → `urgent` to the Dictionary for urgent calls. These bypass
RoomCall Do Not Disturb and quiet hours and use the configured urgent volume.

To follow a person, replace `areas` with `people` → Text → `person.alex`. This
requires room presence such as [Bermuda](https://github.com/agittins/bermuda), or
an explicitly configured fallback room.

## Android

Use the Home Assistant app's **Assist** or a Tasker / Google Assistant routine
that performs `roomcall.broadcast` with the same data.
