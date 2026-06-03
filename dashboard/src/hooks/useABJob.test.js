import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import useABJob from './useABJob.js';

describe('useABJob', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    global.fetch = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts in idle state with no job/images', () => {
    const { result } = renderHook(() => useABJob());
    expect(result.current.status).toBe('idle');
    expect(result.current.jobId).toBeNull();
    expect(result.current.images).toEqual({ a: [], b: [] });
  });

  it('start() sets status=starting then transitions to running with job_id', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ job_id: 'job-123' }),
    });
    const { result } = renderHook(() => useABJob());

    await act(async () => { await result.current.start(1, 'prompt A', 'prompt B'); });

    expect(global.fetch).toHaveBeenCalledWith('/api/ab/generate', expect.objectContaining({ method: 'POST' }));
    expect(result.current.status).toBe('running');
    expect(result.current.jobId).toBe('job-123');
  });

  it('start() sets status=error when response has no job_id', async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, json: async () => ({}) });
    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    expect(result.current.status).toBe('error');
  });

  it('start() sets status=error when response is not ok', async () => {
    global.fetch.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({}) });
    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    expect(result.current.status).toBe('error');
  });

  it('start() sets status=error when fetch throws', async () => {
    global.fetch.mockRejectedValueOnce(new Error('net'));
    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    expect(result.current.status).toBe('error');
  });

  it('polls /api/ab/status/<id> every 2s and stops on ready', async () => {
    global.fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: 'job-1' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'running', images_a: [], images_b: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'ready', images_a: ['a1.png'], images_b: ['b1.png'] }) });

    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    expect(result.current.jobId).toBe('job-1');

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.status).toBe('running');
    expect(result.current.images).toEqual({ a: [], b: [] });

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.status).toBe('ready');
    expect(result.current.images).toEqual({ a: ['a1.png'], b: ['b1.png'] });

    await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
    expect(global.fetch.mock.calls.length).toBe(3);
  });

  it('polls /api/ab/status/<id> and stops on error', async () => {
    global.fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: 'job-2' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'error', images_a: [], images_b: [] }) });

    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.status).toBe('error');
  });

  it('keeps polling when a poll tick throws (network blip)', async () => {
    global.fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: 'job-3' }) })
      .mockRejectedValueOnce(new Error('blip'))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'ready', images_a: ['x'], images_b: ['y'] }) });

    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });

    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.status).toBe('running');
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.status).toBe('ready');
  });

  it('pick() POSTs the choice and resets state on success', async () => {
    global.fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: 'job-4' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'ready', images_a: ['a'], images_b: ['b'] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) });

    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.status).toBe('ready');

    await act(async () => { await result.current.pick('a', 1); });
    expect(result.current.status).toBe('idle');
    expect(result.current.jobId).toBeNull();
    expect(result.current.images).toEqual({ a: [], b: [] });
  });

  it('pick() is a no-op when there is no active job', async () => {
    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.pick('a', 1); });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('keeps state on pick() network error (does not reset)', async () => {
    global.fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: 'job-5' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ status: 'ready', images_a: ['a'], images_b: ['b'] }) })
      .mockRejectedValueOnce(new Error('net'));

    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(result.current.status).toBe('ready');

    await act(async () => { await result.current.pick('a', 1); });
    expect(result.current.status).toBe('ready');
    expect(result.current.jobId).toBe('job-5');
  });

  it('clears any running poll on unmount', async () => {
    global.fetch.mockResolvedValueOnce({ ok: true, json: async () => ({ job_id: 'job-6' }) });
    const { result, unmount } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    expect(result.current.jobId).toBe('job-6');
    const callsBefore = global.fetch.mock.calls.length;
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(global.fetch.mock.calls.length).toBe(callsBefore);
  });
});
