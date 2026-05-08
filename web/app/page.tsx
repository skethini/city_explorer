"use client";

import {
  FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  CitySuggestion,
  createPlan,
  fetchCitySuggestions,
  PlanResponse,
  refinePlan,
} from "../lib/api";

function parseTimeToMinutes(t: string): number | null {
  if (!t) return null;
  const [h, m] = t.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  return h * 60 + m;
}

export default function HomePage() {
  const [query, setQuery] = useState(
    "I want to visit the most beautiful parks in the city"
  );
  const [city, setCity] = useState("Madrid");
  const [mode, setMode] = useState<"walking" | "driving" | "bicycling" | "transit">("walking");
  const [fromTime, setFromTime] = useState("09:00");
  const [toTime, setToTime] = useState("21:00");
  const [result, setResult] = useState<PlanResponse | null>(null);
  const [refineText, setRefineText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refineFlashTick, setRefineFlashTick] = useState(0);
  const [refineToast, setRefineToast] = useState(false);
  const [citySuggestions, setCitySuggestions] = useState<CitySuggestion[]>([]);
  const [cityMenuOpen, setCityMenuOpen] = useState(false);
  const [cityHighlight, setCityHighlight] = useState(-1);
  const [cityLoading, setCityLoading] = useState(false);
  const [cityPickedCoords, setCityPickedCoords] = useState<{ lat: number; lng: number } | null>(
    null
  );
  const cityWrapRef = useRef<HTMLDivElement | null>(null);

  const pickCity = useCallback((s: CitySuggestion) => {
    setCity(s.label);
    setCityPickedCoords({ lat: s.latitude, lng: s.longitude });
    setCityMenuOpen(false);
    setCitySuggestions([]);
    setCityHighlight(-1);
  }, []);

  useEffect(() => {
    if (!refineToast) return;
    const t = window.setTimeout(() => setRefineToast(false), 3200);
    return () => window.clearTimeout(t);
  }, [refineToast]);

  useEffect(() => {
    const q = city.trim();
    if (q.length < 2) {
      setCitySuggestions([]);
      setCityMenuOpen(false);
      setCityLoading(false);
      return;
    }
    let cancelled = false;
    const t = window.setTimeout(async () => {
      setCityLoading(true);
      try {
        const rows = await fetchCitySuggestions(q);
        if (cancelled) return;
        setCitySuggestions(rows);
        setCityMenuOpen(rows.length > 0);
        setCityHighlight(rows.length > 0 ? 0 : -1);
      } finally {
        if (!cancelled) setCityLoading(false);
      }
    }, 280);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [city]);

  useEffect(() => {
    if (!cityMenuOpen) return;
    function onDocMouseDown(ev: MouseEvent) {
      const el = cityWrapRef.current;
      if (el && !el.contains(ev.target as Node)) {
        setCityMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [cityMenuOpen]);

  const mergedQuery = useMemo(() => {
    const from = parseTimeToMinutes(fromTime);
    const to = parseTimeToMinutes(toTime);
    if (from == null || to == null) return `${query} in ${city}`;
    const windowText = `I am free from ${fromTime} to ${toTime}.`;
    return `${windowText} ${query} in ${city}`;
  }, [query, city, fromTime, toTime]);

  async function onPlan(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const payload = {
        query: mergedQuery,
        city,
        mode,
        ...(cityPickedCoords
          ? { lat: cityPickedCoords.lat, lng: cityPickedCoords.lng }
          : {}),
      };
      const data = await createPlan(payload);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to build plan.");
    } finally {
      setLoading(false);
    }
  }

  async function onRefine(e: FormEvent) {
    e.preventDefault();
    if (!result?.session_id || !refineText.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const data = await refinePlan({
        session_id: result.session_id,
        instruction: refineText.trim(),
      });
      setResult(data);
      setRefineText("");
      setRefineFlashTick((n) => n + 1);
      setRefineToast(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refine plan.");
    } finally {
      setLoading(false);
    }
  }

  function onCityKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (!cityMenuOpen || citySuggestions.length === 0) {
      if (e.key === "ArrowDown" && city.trim().length >= 2) {
        setCityMenuOpen(true);
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setCityMenuOpen(false);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCityHighlight((h) => (h + 1) % citySuggestions.length);
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setCityHighlight((h) => (h - 1 + citySuggestions.length) % citySuggestions.length);
      return;
    }
    if (e.key === "Enter") {
      const idx = cityHighlight >= 0 ? cityHighlight : 0;
      const s = citySuggestions[idx];
      if (s) {
        e.preventDefault();
        pickCity(s);
      }
    }
  }

  return (
    <main>
      {refineToast ? (
        <>
          <div className="refine-top-scan" aria-hidden />
          <div className="refine-toast" role="status">
            <span className="refine-toast-icon" aria-hidden>
              ✓
            </span>
            Itinerary updated
          </div>
        </>
      ) : null}

      <h1>City Explorer</h1>
      <p className="hero-description">
        Describe what you want to explore in any city, and City Explorer builds a complete Google
        Maps route with the must-see destinations already mapped out for you.
      </p>

      <form className="card" onSubmit={onPlan}>
        <label>What do you want to do?</label>
        <textarea value={query} onChange={(e) => setQuery(e.target.value)} />

        <div className="row" style={{ marginTop: 12 }}>
          <div className="city-field" ref={cityWrapRef}>
            <label htmlFor="city-input">City</label>
            <div className="city-input-wrap">
              <input
                id="city-input"
                name="city"
                type="text"
                autoComplete="off"
                spellCheck={false}
                value={city}
                onChange={(e) => {
                  setCity(e.target.value);
                  setCityPickedCoords(null);
                }}
                onFocus={() => {
                  if (city.trim().length >= 2 && citySuggestions.length > 0) {
                    setCityMenuOpen(true);
                  }
                }}
                onKeyDown={onCityKeyDown}
                aria-autocomplete="list"
                aria-expanded={cityMenuOpen}
                aria-controls="city-suggestions-list"
              />
              {cityLoading ? <span className="city-loading" aria-hidden /> : null}
              {cityMenuOpen && citySuggestions.length > 0 ? (
                <ul id="city-suggestions-list" className="city-suggestions" role="listbox">
                  {citySuggestions.map((s, i) => (
                    <li
                      key={`${s.latitude},${s.longitude},${s.label}`}
                      role="option"
                      aria-selected={i === cityHighlight}
                      className={i === cityHighlight ? "city-suggestion is-highlighted" : "city-suggestion"}
                      onMouseEnter={() => setCityHighlight(i)}
                      onMouseDown={(ev) => {
                        ev.preventDefault();
                        pickCity(s);
                      }}
                    >
                      <span className="city-suggestion-name">{s.name}</span>
                      <span className="city-suggestion-meta">
                        {[s.admin1, s.country].filter(Boolean).join(" · ")}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </div>
          <div>
            <label>Travel mode</label>
            <select value={mode} onChange={(e) => setMode(e.target.value as any)}>
              <option value="walking">walking</option>
              <option value="driving">driving</option>
              <option value="bicycling">bicycling</option>
              <option value="transit">transit</option>
            </select>
          </div>
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label>Free from</label>
            <input type="time" value={fromTime} onChange={(e) => setFromTime(e.target.value)} />
          </div>
          <div>
            <label>Free until</label>
            <input type="time" value={toTime} onChange={(e) => setToTime(e.target.value)} />
          </div>
        </div>

        <div style={{ marginTop: 14 }}>
          <button type="submit" disabled={loading}>
            {loading ? "Planning..." : "Build My Walking Tour"}
          </button>
        </div>
      </form>

      {error ? <div className="card" style={{ borderColor: "#f2c3c3" }}>{error}</div> : null}

      {result ? (
        <>
          <section
            className={`card itinerary-card ${
              refineFlashTick ? `itinerary-card--flash-${refineFlashTick % 2}` : ""
            }`}
          >
            <h2>Itinerary</h2>
            <a href={result.gmaps_url} target="_blank" rel="noreferrer" className="maps-cta-link">
              <button className="maps-cta-button" type="button">
                Open Full Route in Google Maps
              </button>
            </a>
            {[...result.itinerary.stops]
              .sort((a, b) => a.order - b.order)
              .map((s) => (
                <div key={`${s.order}-${s.place.name}`} className="itinerary-stop">
                  <div style={{ marginBottom: 6 }}>
                    <span style={{ fontWeight: 600, color: "#586074" }}>{s.order}. </span>
                    <a
                      className="destination-title-link"
                      href={mapsPlaceUrl(s.place.name, s.place.lat, s.place.lng)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {s.place.name}
                    </a>
                    {s.arrive_after && s.arrive_after !== "any" ? (
                      <span className="muted" style={{ marginLeft: 8 }}>
                        ({s.arrive_after})
                      </span>
                    ) : null}
                  </div>
                  {s.place.description ? (
                    <p className="muted" style={{ margin: "4px 0 0", lineHeight: 1.45 }}>
                      {s.place.description}
                    </p>
                  ) : null}
                  {s.place.image_url ? (
                    <a
                      href={mapsPlaceUrl(s.place.name, s.place.lat, s.place.lng)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <img
                        className="itinerary-stop-image"
                        src={s.place.image_url}
                        alt={s.place.name}
                        loading="lazy"
                      />
                    </a>
                  ) : null}
                </div>
              ))}
          </section>

          {result.schedule?.length ? (
            <section
              className={`card schedule-section ${
                refineFlashTick ? `schedule-section--flash-${refineFlashTick % 2}` : ""
              }`}
            >
              <h2>Schedule</h2>
              <ol className="schedule-list">
                {result.schedule.map((row, idx) => {
                  const match = result.itinerary.stops.find((s) => s.place.name === row.place_name);
                  const href = match
                    ? mapsPlaceUrl(match.place.name, match.place.lat, match.place.lng)
                    : undefined;
                  return (
                    <li key={`${idx}-${row.time_start}-${row.place_name}`} className="schedule-item">
                      <div className="schedule-time">
                        <span>{row.time_start}</span>
                        <span className="schedule-sep" aria-hidden>
                          →
                        </span>
                        <span>{row.time_end}</span>
                      </div>
                      <div className="schedule-body">
                        {href ? (
                          <a className="schedule-place-link" href={href} target="_blank" rel="noreferrer">
                            {row.place_name}
                          </a>
                        ) : (
                          <span className="schedule-place">{row.place_name}</span>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ol>
            </section>
          ) : null}

          <form className="card" onSubmit={onRefine}>
            <h3>Refine this plan</h3>
            <input
              value={refineText}
              onChange={(e) => setRefineText(e.target.value)}
              placeholder="e.g. Add a Thai lunch near stop 3"
            />
            <div style={{ marginTop: 12 }}>
              <button type="submit" disabled={loading}>
                {loading ? "Updating..." : "Refine"}
              </button>
            </div>
          </form>
        </>
      ) : null}
    </main>
  );
}

function mapsPlaceUrl(name: string, lat: number, lng: number): string {
  // Use place-name search so users land on a listing page with reviews/photos.
  const query = encodeURIComponent(`${name} near ${lat.toFixed(5)},${lng.toFixed(5)}`);
  return `https://www.google.com/maps/search/?api=1&query=${query}`;
}
