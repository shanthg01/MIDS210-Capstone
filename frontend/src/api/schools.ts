import client from './client';
import type { RosterGapResponse, SchoolListResponse, TeamSystemProfileResponse } from '../types/api';

// Public — used by the signup picker, before a user has a token.
export async function listSchools(): Promise<SchoolListResponse> {
  const { data } = await client.get<SchoolListResponse>('/schools');
  return data;
}

// 404 means the caller's program has no school or no roster snapshot yet —
// expected for dev/test accounts, not an error to surface.
export async function getRosterGap(): Promise<RosterGapResponse> {
  const { data } = await client.get<RosterGapResponse>('/schools/roster-gap');
  return data;
}

// 404 means the caller's program has no school or no team_system_profiles row
// yet (Model #2 hasn't run for that school/season) — not an error to surface.
export async function getSystemProfile(): Promise<TeamSystemProfileResponse> {
  const { data } = await client.get<TeamSystemProfileResponse>('/schools/system-profile');
  return data;
}
