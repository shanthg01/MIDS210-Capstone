import client from './client';
import type { LoginRequest, SignupRequest, TokenResponse } from '../types/api';

export async function login(body: LoginRequest): Promise<TokenResponse> {
  const { data } = await client.post<TokenResponse>('/auth/login', body);
  return data;
}

export async function signup(body: SignupRequest): Promise<TokenResponse> {
  const { data } = await client.post<TokenResponse>('/auth/signup', body);
  return data;
}

export async function logout(): Promise<void> {
  await client.post('/auth/logout').catch(() => {});
}
