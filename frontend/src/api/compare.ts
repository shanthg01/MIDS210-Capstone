import client from './client';
import type { CompareRequest, CompareResponse } from '../types/api';

export async function comparePlayers(req: CompareRequest): Promise<CompareResponse> {
  const { data } = await client.post<CompareResponse>('/compare', req);
  return data;
}
