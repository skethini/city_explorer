# Shortcut: Plan City Tour

Prompts for a request, calls `POST BACKEND/plan`, and opens the resulting
Google Maps route. Stores the returned `session_id` so `Refine City Tour`
can pick up from here.

## Setup

1. Open **Shortcuts** on iPhone.
2. Tap **+** to create a new shortcut and name it **Plan City Tour**.
3. Add the following actions, in order. Action names match exactly what you
   see in the Shortcuts action picker.

## Actions

1. **Get Current Location**
   - No options to set.

2. **Ask for Input**
   - Input Type: `Text`
   - Prompt: `What do you want to do in this city?`
   - Default Answer: leave blank

3. **Dictionary** (creates the request body)
   - `query` -> `Provided Input` (from step 2)
   - `lat` -> `Magic Variable` -> `Current Location` -> `Latitude`
   - `lng` -> `Magic Variable` -> `Current Location` -> `Longitude`

4. **Get Contents of URL**
   - URL: `BACKEND/plan`
   - Method: `POST`
   - Request Body: `JSON` -> the Dictionary from step 3
   - Headers: `Content-Type` = `application/json`

5. **Get Dictionary Value**
   - Get: `Value`
   - Key: `summary`
   - From: result of step 4
   - Rename the magic variable to `Summary`.

6. **Get Dictionary Value**
   - Get: `Value`
   - Key: `gmaps_urls`
   - From: result of step 4
   - Rename to `MapsUrls`.

7. **Get Dictionary Value**
   - Get: `Value`
   - Key: `session_id`
   - From: result of step 4
   - Rename to `SessionId`.

8. **Set Variable**
   - Variable Name: `CityExplorerSession`
   - Value: `SessionId`
   - (This persists across the two Shortcuts inside the same session run; for
     cross-Shortcut persistence install the free
     [Data Jar](https://apps.apple.com/us/app/data-jar/id1453273704) app and
     replace this with `Set Data Jar Value`.)

9. **Choose from Menu**
   - Prompt: `Summary`
   - Items:
     - `Open in Maps`
     - `Refine`
     - `Cancel`

10. Inside the **Open in Maps** branch:
    - **Repeat with Each** -> `MapsUrls`
      - **Open URLs** -> `Repeat Item`
      - **Wait** -> `2 seconds` (lets Google Maps catch up between chunks)

11. Inside the **Refine** branch:
    - **Run Shortcut** -> `Refine City Tour`

12. Inside the **Cancel** branch:
    - **Stop This Shortcut**

## Triggering it

- **Siri**: Settings > Siri & Search > Plan City Tour > Add to Siri ->
  record `Plan my tour`.
- **Home Screen**: long-press the Shortcut > **Add to Home Screen**.
- **Share Sheet**: in the Shortcut's settings, enable
  *Use with Share Sheet* if you want to plan a tour around an address you've
  shared from another app.
