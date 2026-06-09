import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import useStatusPolling from './useStatusPolling.js';

async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

describe('useStatusPolling', () => {
  const STATUS_RESPONSE = { status: 'idle', logs: [], output_video: null, active_question: null };
  const JOBS_RESPONSE = { jobs: [] };

  let fetchMock;
  beforeEach(() => {
    fetchMock = vi.fn((url) => {
      if (url === '/api/jobs?limit=1') {
        return Promise.resolve({ ok: true, json: async () => JOBS_RESPONSE });
      }
      return Promise.resolve({ ok: true, json: async () => STATUS_RESPONSE });
    });
    global.fetch = fetchMock;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts with the idle default state', async () => {
    const { result } = renderHook(() => useStatusPolling());
    await act(async () => {});
    expect(result.current[0]).toEqual({
      state: 'idle',
      logs: [],
      video: null,
      active_question: null,
      latestJob: null,
    });
  });

  it('fetches /api/status and /api/jobs immediately on mount', async () => {
    renderHook(() => useStatusPolling());
    await flush();
    expect(fetchMock).toHaveBeenCalledWith('/api/status');
    expect(fetchMock).toHaveBeenCalledWith('/api/jobs?limit=1');
  });

  it('updates state with mapped fields from a successful response', async () => {
    const statusData = {
      status: 'running',
      logs: ['seg1 done', 'seg2 done'],
      output_video: '/studio_outputs/final.mp4',
      active_question: 'continue?',
    };
    fetchMock.mockReset();
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => statusData })
      .mockResolvedValueOnce({ ok: true, json: async () => JOBS_RESPONSE });
    const { result } = renderHook(() => useStatusPolling());
    await flush();
    expect(result.current[0]).toEqual({
      state: 'running',
      logs: ['seg1 done', 'seg2 done'],
      video: '/studio_outputs/final.mp4',
      active_question: 'continue?',
      latestJob: null,
    });
  });

  it('polls at ~1.5s intervals', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    renderHook(() => useStatusPolling());
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(fetchMock).toHaveBeenCalledTimes(4);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(fetchMock).toHaveBeenCalledTimes(6);
  });

  async function testPollingErrorResilience(prevStateValue, errorResponse) {
    fetchMock.mockReset();
    const statusResp = { ok: true, json: async () => ({ status: prevStateValue, logs: prevStateValue === 'running' ? ['a'] : [], output_video: null, active_question: null }) };
    fetchMock
      .mockResolvedValueOnce(statusResp)       // /api/status (mount tick)
      .mockResolvedValueOnce({ ok: true, json: async () => JOBS_RESPONSE })  // /api/jobs (mount tick)
      .mockResolvedValueOnce(statusResp)       // /api/status (2nd tick)
      .mockResolvedValueOnce(errorResponse);   // /api/jobs (2nd tick, error)
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const { result } = renderHook(() => useStatusPolling());
    await flush();
    expect(result.current[0].state).toBe(prevStateValue);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(result.current[0].state).toBe(prevStateValue);
    return result;
  }

  it('keeps last known state when polling throws', async () => {
    const result = await testPollingErrorResilience('running', () => Promise.reject(new Error('boom')));
    expect(result.current[0].logs).toEqual(['a']);
  });

  it('keeps last known state when response is not ok', async () => {
    await testPollingErrorResilience('paused', () => Promise.resolve({ ok: false, status: 500, json: async () => ({}) }));
  });

  it('clears the interval on unmount', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const { unmount } = renderHook(() => useStatusPolling());
    await flush();
    // mount triggers 2 calls: /api/status + /api/jobs?limit=1
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const callsAtUnmount = fetchMock.mock.calls.length;
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock.mock.calls.length).toBe(callsAtUnmount);
  });
});
