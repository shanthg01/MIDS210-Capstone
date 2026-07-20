import client from './client';
import type { AgentRunAccepted, AgentRunRequest, AgentRunStatus, ProgramEventsResponse } from '../types/api';

export async function startNewsMonitoringRun(body: AgentRunRequest = {}): Promise<AgentRunAccepted> {
  const { data } = await client.post<AgentRunAccepted>('/agent/news-monitoring/run', body);
  return data;
}

export async function getNewsMonitoringRun(runId: string): Promise<AgentRunStatus> {
  const { data } = await client.get<AgentRunStatus>(`/agent/news-monitoring/runs/${runId}`);
  return data;
}

export async function getNewsMonitoringEvents(schoolId?: number, limit = 25): Promise<ProgramEventsResponse> {
  const { data } = await client.get<ProgramEventsResponse>('/agent/news-monitoring/events', {
    params: { school_id: schoolId, limit },
  });
  return data;
}
