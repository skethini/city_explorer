export type PlanResponse = {
  session_id: string;
  summary: string;
  itinerary_text: string;
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
      place: {
        name: string;
        category: string;
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

async function safeJson(res: Response): Promise<any> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}
