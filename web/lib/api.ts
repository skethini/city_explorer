export type ScheduleSlot = {
  time_start: string;
  time_end: string;
  place_name: string;
};

export type CitySuggestion = {
  label: string;
  name: string;
  country: string | null;
  admin1: string | null;
  latitude: number;
  longitude: number;
};

export type PlanResponse = {
  session_id: string;
  summary: string;
  itinerary_text: string;
  schedule: ScheduleSlot[];
  gmaps_url: string;
  gmaps_urls: string[];
  itinerary: {
    travel_mode: string;
    total_distance_m: number;
    total_duration_s: number;
    estimated_visit_duration_s?: number;
    estimated_total_duration_s?: number;
    target_duration_s?: number | null;
    stops: Array<{
      order: number;
      arrive_after?: string;
      place: {
        name: string;
        category: string;
        lat: number;
        lng: number;
        description?: string | null;
        image_url?: string | null;
      };
    }>;
  };
};

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:8000";

export async function createPlan(payload: {
  query: string;
  city: string;
  mode: "walking" | "driving" | "bicycling" | "transit";
  lat?: number;
  lng?: number;
}): Promise<PlanResponse> {
  const res = await fetch(`${API_BASE}/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await safeJson(res);
    throw new Error(body?.detail || `Plan failed (${res.status})`);
  }
  return res.json();
}

export async function refinePlan(payload: {
  session_id: string;
  instruction: string;
}): Promise<PlanResponse> {
  const res = await fetch(`${API_BASE}/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await safeJson(res);
    throw new Error(body?.detail || `Refine failed (${res.status})`);
  }
  return res.json();
}

export async function fetchCitySuggestions(q: string, limit = 10): Promise<CitySuggestion[]> {
  const trimmed = q.trim();
  if (trimmed.length < 2) {
    return [];
  }
  const params = new URLSearchParams({ q: trimmed, limit: String(limit) });
  const res = await fetch(`${API_BASE}/city-suggestions?${params.toString()}`);
  if (!res.ok) {
    return [];
  }
  return res.json();
}

async function safeJson(res: Response): Promise<any> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}
