import client from './client';
import type { RecommendationsResponse } from '../types/api';

export async function getRecommendations(userId: number): Promise<RecommendationsResponse> {
  const { data } = await client.get<RecommendationsResponse>('/recommendations', {
    params: { user_id: userId },
  });
  return data;
}
