import client from './client';
import type {
  PlayerProfile,
  PlayerProjectionResponse,
  PlayerSearchResponse,
  PlayingTimeOverrideRequest,
  PlayingTimeOverrideResponse,
  StatThreshold,
} from '../types/api';

export async function searchPlayers(
  name: string,
  options?: { availableOnly?: boolean; minStats?: StatThreshold[] },
): Promise<PlayerSearchResponse> {
  // Axios's default object-param serializer emits `min_stat[]=`, which FastAPI's
  // `Query(list[str])` won't parse — URLSearchParams gives the plain repeated-key
  // form FastAPI expects (confirmed: axios passes a URLSearchParams through as-is).
  const params = new URLSearchParams();
  params.append('name', name);
  if (options?.availableOnly) params.append('available_only', 'true');
  for (const t of options?.minStats ?? []) {
    params.append('min_stat', `${t.stat}:${t.min_value}`);
  }
  const { data } = await client.get<PlayerSearchResponse>('/players/search', { params });
  return data;
}

export async function getPlayer(playerId: string): Promise<PlayerProfile> {
  const { data } = await client.get<PlayerProfile>(`/players/${playerId}`);
  return data;
}

// 404 means no projection row for this player — real "not available", not an error to surface.
// Omit schoolId for the neutral (context-free) projection; pass it for the
// destination-adjusted projection (school-specific, includes projected_minutes/usage).
// minutesOverride (destination mode only) recomputes projected_box_score_at_minutes
// for a hypothetical minutes value — no model rerun, see recompute_box_score_for_minutes.
export async function getPlayerProjection(
  playerId: string,
  schoolId?: number,
  minutesOverride?: number,
): Promise<PlayerProjectionResponse> {
  const params: Record<string, number> = {};
  if (schoolId !== undefined) params.school_id = schoolId;
  if (minutesOverride !== undefined) params.minutes_override = minutesOverride;
  const { data } = await client.get<PlayerProjectionResponse>(`/players/${playerId}/projection`, {
    params: Object.keys(params).length > 0 ? params : undefined,
  });
  return data;
}

// Coach "what if" minutes override — response-only, doesn't persist to
// playing_time_projections. See modeling.playing_time.compute_role_fit_override
// for why this is a delta on the stored role_fit, not a full recompute.
export async function overridePlayingTime(
  playerId: string,
  body: PlayingTimeOverrideRequest,
): Promise<PlayingTimeOverrideResponse> {
  const { data } = await client.post<PlayingTimeOverrideResponse>(
    `/players/${playerId}/playing-time/override`,
    body,
  );
  return data;
}
