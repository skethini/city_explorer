# Shortcut: Refine City Tour

Reads the saved `session_id`, prompts for a follow-up instruction, calls
`POST https://city-explorer-t9oj.onrender.com/refine`, and opens the updated route.

## Setup

1. Open **Shortcuts** on iPhone.
2. Tap **+**, name it **Refine City Tour**.

## Actions

1. **Ask for Input**
   - Input Type: `Text`
   - Prompt: `What should I change?`
   - Default Answer: blank

2. **Get Variable** -> `CityExplorerSession`
   - If you used Data Jar in `Plan City Tour`, swap this for **Get Data Jar
     Value** with key `CityExplorerSession`.
   - Rename the magic variable to `SessionId`.

3. **Dictionary**
   - `session_id` -> `SessionId`
   - `instruction` -> `Provided Input`

4. **Get Contents of URL**
   - URL: `https://city-explorer-t9oj.onrender.com/refine`
   - Method: `POST`
   - Request Body: `JSON` -> Dictionary from step 3
   - Headers: `Content-Type` = `application/json`

5. **Get Dictionary Value** -> `summary` -> `Summary`
6. **Get Dictionary Value** -> `gmaps_urls` -> `MapsUrls`

7. **Choose from Menu**
   - Prompt: `Summary`
   - Items:
     - `Open in Maps`
     - `Refine again`
     - `Cancel`

8. Inside **Open in Maps**:
   - **Repeat with Each** -> `MapsUrls`
     - **Open URLs** -> `Repeat Item`
     - **Wait** -> `2 seconds`

9. Inside **Refine again**:
   - **Run Shortcut** -> `Refine City Tour` (recursive — Shortcuts handles
     this fine; the loop ends when the user picks Cancel).

10. Inside **Cancel**:
    - **Stop This Shortcut**

## Tips

- If `Get Variable` returns nothing, the `Plan City Tour` Shortcut hasn't
  been run in this session. The Shortcut will surface a 404 from the
  backend; add a **Show Alert** before step 3 if you want a friendlier
  message.
- Add this Shortcut to Siri with the phrase `Adjust my tour`.
