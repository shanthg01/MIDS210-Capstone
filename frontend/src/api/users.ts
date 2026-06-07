import client from './client';
import type { ShortlistResponse, ShortlistItem, UserPreferences, UserPreferencesUpdate } from '../types/api';

export async function getShortlist(userId: number): Promise<ShortlistResponse> {
  const { data } = await client.get<ShortlistResponse>(`/users/${userId}/shortlist`);
  return data;
}

export async function addToShortlist(userId: number, playerId: number): Promise<ShortlistItem> {
  const { data } = await client.post<ShortlistItem>(`/users/${userId}/shortlist/${playerId}`);
  return data;
}

export async function removeFromShortlist(userId: number, playerId: number): Promise<void> {
  await client.delete(`/users/${userId}/shortlist/${playerId}`);
}

export async function getPreferences(userId: number): Promise<UserPreferences> {
  const { data } = await client.get<UserPreferences>(`/users/${userId}/preferences`);
  return data;
}

export async function updatePreferences(
  userId: number,
  body: UserPreferencesUpdate,
): Promise<UserPreferences> {
  const { data } = await client.put<UserPreferences>(`/users/${userId}/preferences`, body);
  return data;
}
