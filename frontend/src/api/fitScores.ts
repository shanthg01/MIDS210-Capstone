import client from './client';
import type {
  FitScoreResponse,
  RosterImpactResponse,
  TeamRatingOverrideRequest,
  TeamRatingOverrideResponse,
  TeamRatingProjectionResponse,
} from '../types/api';

export async function getFitScore(
  playerId: string,
  schoolId: number,
): Promise<FitScoreResponse> {
  const { data } = await client.get<FitScoreResponse>('/fit-scores', {
    params: { player_id: playerId, school_id: schoolId },
  });
  return data;
}

export async function getTeamRatingProjection(
  playerId: string,
  schoolId: number,
): Promise<TeamRatingProjectionResponse> {
  const { data } = await client.get<TeamRatingProjectionResponse>('/projections/team-rating', {
    params: { player_id: playerId, school_id: schoolId },
  });
  return data;
}

// Coach "what if" minutes override — real school-scoped re-inference against
// the persisted @champion Ridge models (team_rating_projections only stores
// scalar outputs, so this isn't a cheap delta like the Role Fit override).
export async function overrideTeamRating(
  body: TeamRatingOverrideRequest,
): Promise<TeamRatingOverrideResponse> {
  const { data } = await client.post<TeamRatingOverrideResponse>('/projections/team-rating/override', body);
  return data;
}

export async function getTopRosterImpact(
  season = 2027,
  limit = 25,
): Promise<RosterImpactResponse> {
  const { data } = await client.get<RosterImpactResponse>('/projections/team-rating/top', {
    params: { season, limit },
  });
  return data;
}
