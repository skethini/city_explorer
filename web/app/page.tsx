"use client";

import { FormEvent, useMemo, useState } from "react";

import { createPlan, PlanResponse, refinePlan } from "../lib/api";

function parseTimeToMinutes(t: string): number | null {
  if (!t) return null;
  const [h, m] = t.split(":").map(Number);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  return h * 60 + m;
}

export default function HomePage() {
  const [query, setQuery] = useState(
    "I'm free from 9am to 9pm and want to see the most important places in Madrid."
  );
  const [city, setCity] = useState("Madrid");
  const [mode, setMode] = useState<"walking" | "driving" | "bicycling" | "transit">("walking");
  const [fromTime, setFromTime] = useState("09:00");
  const [toTime, setToTime] = useState("21:00");
  const [result, setResult] = useState<PlanResponse | null>(null);
  const [refineText, setRefineText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to refine plan.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <h1>City Explorer</h1>
      <p className="muted">Shareable MVP for route planning with your FastAPI backend.</p>

      <form className="card" onSubmit={onPlan}>
        <label>What do you want to do?</label>
        <textarea value={query} onChange={(e) => setQuery(e.target.value)} />

        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label>City</label>
            <input value={city} onChange={(e) => setCity(e.target.value)} />
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
          <section className="card">
            <h2>Itinerary</h2>
            <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>{result.itinerary_text || result.summary}</pre>
            <p className="muted" style={{ marginTop: 12 }}>
              Distance: {(result.itinerary.total_distance_m / 1000).toFixed(1)} km | Transit:{" "}
              {(result.itinerary.total_duration_s / 60).toFixed(0)} min | Estimated total:{" "}
              {((result.itinerary.estimated_total_duration_s || 0) / 60).toFixed(0)} min
            </p>
            <a href={result.gmaps_url} target="_blank" rel="noreferrer">
              <button className="secondary" type="button">
                Open in Google Maps
              </button>
            </a>
          </section>

          <section className="card">
            <h3>Stops</h3>
            <ol className="stops">
              {result.itinerary.stops.map((s) => (
                <li key={`${s.order}-${s.place.name}`}>
                  {s.place.name} <span className="muted">({s.place.category})</span>
                </li>
              ))}
            </ol>
          </section>

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
