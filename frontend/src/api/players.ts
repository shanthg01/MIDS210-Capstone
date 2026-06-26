import client from './client';
import type { PlayerProfile, PlayerProjectionResponse, PlayerSearchResponse } from '../types/api';

export async function searchPlayers(name: string): Promise<PlayerSearchResponse> {
  const { data } = await client.get<PlayerSearchResponse>('/players/search', {
    params: { name },
  });
  return data;
}

export async function getPlayer(playerId: number): Promise<PlayerProfile> {
  const { data } = await client.get<PlayerProfile>(`/players/${playerId}`);
  return data;
}

// 404 means no projection row for this player — real "not available", not an error to surface.
export async function getPlayerProjection(playerId: number): Promise<PlayerProjectionResponse> {
  const { data } = await client.get<PlayerProjectionResponse>(`/players/${playerId}/projection`);
  return data;
}
