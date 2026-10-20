# Siri, Apple Watch and CarPlay

Use Siri to call a room from your iPhone, Apple Watch, CarPlay or HomePod: *"Hey Siri,
intercom basement"*, then say your message. Siri turns it into text and RoomCall speaks it
in that room, with the chime first.

This uses the **Home Assistant** app's Shortcuts actions, so it works wherever Siri can
run a shortcut, including on the go over your remote connection.

## Make the shortcut

1. Open **Shortcuts** → **+**.
2. Add **Dictate Text**. Set *Stop Listening* to **After Pause**.
3. Add the Home Assistant app's **Perform Action** (called *Call Service* in older app versions):
    - **Action:** `roomcall.broadcast`
    - **Data:**

      ```json
      {"areas": ["basement"], "message": "Dictated Text"}
      ```

      Tap `Dictated Text` and pick the **Dictated Text** variable from step 2.
4. Name the shortcut after what you'll say, for example **Intercom basement**.

Say *"Hey Siri, intercom basement"*, wait for the beep, and talk.

!!! tip "One shortcut for every room"
    Start with **Choose from Menu** (Basement, Kitchen, Everywhere…). In each branch set a
    `room` text variable, then use `{"areas": ["room"], "message": "Dictated Text"}` with both
    variables. Name it **Intercom** and Siri will ask which room.

## Urgent calls

Add `"priority": "urgent"` to the data. Urgent calls play at the urgent volume everywhere you
send them, even with Do Not Disturb on or during quiet hours.

## Call a person

Replace `areas` with `"people": ["person.alex"]` to play wherever Alex is right now (needs room
presence such as [Bermuda](https://github.com/agittins/bermuda)).

## Android

Use the Home Assistant app's **Assist** or a Tasker / Google Assistant routine that performs
`roomcall.broadcast` with the same data.
