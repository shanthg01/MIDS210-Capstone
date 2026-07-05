import client from './client';
import type { PlayerProfile, PlayerProjectionResponse, PlayerSearchResponse, StatThreshold } from '../types/api';

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
export async function getPlayerProjection(playerId: string): Promise<PlayerProjectionResponse> {
  const { data } = await client.get<PlayerProjectionResponse>(`/players/${playerId}/projection`);
  return data;
}
