import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import useABJob from './useABJob.js';

const okJson = (data) => ({ ok: true, json: async () => data });
const ok = () => okJson({});

async function startJob(...responses) {
  for (const r of responses) {
    if (r instanceof Error) global.fetch.mockRejectedValueOnce(r);
    else global.fetch.mockResolvedValueOnce(r);
  }
  const { result } = renderHook(() => useABJob());
  await act(async () => { await result.current.start(1, 'a', 'b'); });
  return result;
}

async function advanceAndAct(ms) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

function expectIdleState(result) {
  expect(result.current.status).toBe('idle');
  expect(result.current.jobId).toBeNull();
  expect(result.current.images).toEqual({ a: [], b: [] });
}

async function awaitReadyAndActPick(result) {
  await advanceAndAct(2000);
  expect(result.current.status).toBe('ready');
  await act(async () => { await result.current.pick('a', 1); });
}

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
    expectIdleState(result);
  });

  it('start() sets status=starting then transitions to running with job_id', async () => {
    const result = await startJob(okJson({ job_id: 'job-123' }));
    expect(global.fetch).toHaveBeenCalledWith('/api/ab/generate', expect.objectContaining({ method: 'POST' }));
    expect(result.current.status).toBe('running');
    expect(result.current.jobId).toBe('job-123');
  });

  it('start() sets status=error when response has no job_id', async () => {
    const result = await startJob(okJson({}));
    expect(result.current.status).toBe('error');
  });

  it('start() sets status=error when response is not ok', async () => {
    const result = await startJob({ ok: false, status: 500, json: async () => ({}) });
    expect(result.current.status).toBe('error');
  });

  it('start() sets status=error when fetch throws', async () => {
    global.fetch.mockRejectedValueOnce(new Error('net'));
    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    expect(result.current.status).toBe('error');
  });

  it('polls /api/ab/status/<id> every 2s and stops on ready', async () => {
    const result = await startJob(
      okJson({ job_id: 'job-1' }),
      okJson({ status: 'running', images_a: [], images_b: [] }),
      okJson({ status: 'ready', images_a: ['a1.png'], images_b: ['b1.png'] }),
    );
    expect(result.current.jobId).toBe('job-1');

    await advanceAndAct(2000);
    expect(result.current.status).toBe('running');
    expect(result.current.images).toEqual({ a: [], b: [] });

    await advanceAndAct(2000);
    expect(result.current.status).toBe('ready');
    expect(result.current.images).toEqual({ a: ['a1.png'], b: ['b1.png'] });

    await advanceAndAct(4000);
    expect(global.fetch.mock.calls.length).toBe(3);
  });

  it('polls /api/ab/status/<id> and stops on error', async () => {
    const result = await startJob(
      okJson({ job_id: 'job-2' }),
      okJson({ status: 'error', images_a: [], images_b: [] }),
    );
    await advanceAndAct(2000);
    expect(result.current.status).toBe('error');
  });

  it('keeps polling when a poll tick throws (network blip)', async () => {
    const result = await startJob(
      okJson({ job_id: 'job-3' }),
      new Error('blip'),
      okJson({ status: 'ready', images_a: ['x'], images_b: ['y'] }),
    );
    await advanceAndAct(2000);
    expect(result.current.status).toBe('running');
    await advanceAndAct(2000);
    expect(result.current.status).toBe('ready');
  });

  it('pick() POSTs the choice and resets state on success', async () => {
    const result = await startJob(
      okJson({ job_id: 'job-4' }),
      okJson({ status: 'ready', images_a: ['a'], images_b: ['b'] }),
      ok(),
    );
    await awaitReadyAndActPick(result);
    expectIdleState(result);
  });

  it('pick() is a no-op when there is no active job', async () => {
    const { result } = renderHook(() => useABJob());
    await act(async () => { await result.current.pick('a', 1); });
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('keeps state on pick() network error (does not reset)', async () => {
    const result = await startJob(
      okJson({ job_id: 'job-5' }),
      okJson({ status: 'ready', images_a: ['a'], images_b: ['b'] }),
      new Error('net'),
    );
    await awaitReadyAndActPick(result);
    expect(result.current.status).toBe('ready');
    expect(result.current.jobId).toBe('job-5');
  });

  it('clears any running poll on unmount', async () => {
    global.fetch.mockResolvedValueOnce(okJson({ job_id: 'job-6' }));
    const { result, unmount } = renderHook(() => useABJob());
    await act(async () => { await result.current.start(1, 'a', 'b'); });
    expect(result.current.jobId).toBe('job-6');
    const callsBefore = global.fetch.mock.calls.length;
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(global.fetch.mock.calls.length).toBe(callsBefore);
  });
});
