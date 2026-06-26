import client from './client';
import type { RosterGapResponse } from '../types/api';

// 404 means the caller's program has no school or no roster snapshot yet —
// expected for dev/test accounts, not an error to surface.
export async function getRosterGap(): Promise<RosterGapResponse> {
  const { data } = await client.get<RosterGapResponse>('/schools/roster-gap');
  return data;
}
