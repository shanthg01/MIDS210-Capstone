import client from './client';
import type { PlayerProfile, PlayerSearchResponse } from '../types/api';

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
