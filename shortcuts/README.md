# iOS Shortcuts

City Explorer ships with two iOS Shortcuts. Recreate them by hand with the
guides below — Apple does not provide a stable importable file format that
can be authored from a code repository, so the action-by-action lists are
the source of truth.

You will need:

- An iPhone running iOS 17 or later (the Shortcuts app comes pre-installed).
- The HTTPS URL of your deployed City Explorer backend, e.g.
  `https://city-explorer.onrender.com`. Replace `BACKEND` in every step
  below with that URL.

After creating both Shortcuts, add them to your Home Screen and/or set up a
Siri phrase ("Plan my city tour") in Settings > Siri & Search.

| File                                                | Purpose                                       |
| --------------------------------------------------- | --------------------------------------------- |
| [`Plan-City-Tour.md`](Plan-City-Tour.md)            | Entry point: prompt, plan, open in Maps.      |
| [`Refine-City-Tour.md`](Refine-City-Tour.md)        | Follow-up edits to the most recent itinerary. |
