import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import useStatusPolling from './useStatusPolling.js';

async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

describe('useStatusPolling', () => {
  let fetchMock;
  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'idle', logs: [], output_video: null, active_question: null }),
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
    });
  });

  it('fetches /api/status immediately on mount', async () => {
    renderHook(() => useStatusPolling());
    await flush();
    expect(fetchMock).toHaveBeenCalledWith('/api/status');
  });

  it('updates state with mapped fields from a successful response', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        status: 'running',
        logs: ['seg1 done', 'seg2 done'],
        output_video: '/studio_outputs/final.mp4',
        active_question: 'continue?',
      }),
    });
    const { result } = renderHook(() => useStatusPolling());
    await flush();
    expect(result.current[0]).toEqual({
      state: 'running',
      logs: ['seg1 done', 'seg2 done'],
      video: '/studio_outputs/final.mp4',
      active_question: 'continue?',
    });
  });

  it('polls at ~1.5s intervals', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    renderHook(() => useStatusPolling());
    await flush();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  async function testPollingErrorResilience(prevStateValue, errorResponse) {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: prevStateValue, logs: prevStateValue === 'running' ? ['a'] : [], output_video: null, active_question: null }),
      })
      .mockImplementationOnce(errorResponse);
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const { result } = renderHook(() => useStatusPolling());
    await flush();
    expect(result.current[0].state).toBe(prevStateValue);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
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
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const callsAtUnmount = fetchMock.mock.calls.length;
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock.mock.calls.length).toBe(callsAtUnmount);
  });
});
