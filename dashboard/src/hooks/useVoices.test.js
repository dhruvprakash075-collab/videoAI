import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import useVoices from './useVoices.js';

describe('useVoices', () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it('starts with an empty voices list', () => {
    global.fetch.mockResolvedValue({ ok: true, json: async () => ({ voices: [] }) });
    const { result } = renderHook(() => useVoices());
    expect(result.current.voices).toEqual([]);
  });

  it('fetches /api/voices on mount and populates state', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ voices: [{ name: 'alice', size: 1024 }, { name: 'bob', size: 2048 }] }),
    });
    const { result } = renderHook(() => useVoices());
    await waitFor(() => expect(result.current.voices).toHaveLength(2));
    expect(result.current.voices[0].name).toBe('alice');
  });

  it('refresh() re-fetches and updates state', async () => {
    global.fetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ voices: [{ name: 'a' }] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ voices: [{ name: 'a' }, { name: 'b' }] }) });

    const { result } = renderHook(() => useVoices());
    await waitFor(() => expect(result.current.voices).toHaveLength(1));

    await act(async () => { await result.current.refresh(); });
    expect(result.current.voices).toHaveLength(2);
  });

  it('falls back to [] when the response has no voices key', async () => {
    global.fetch.mockResolvedValue({ ok: true, json: async () => ({}) });
    const { result } = renderHook(() => useVoices());
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(result.current.voices).toEqual([]);
  });

  it('does not crash and stays empty when fetch rejects', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    global.fetch.mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useVoices());
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(result.current.voices).toEqual([]);
    expect(errSpy).toHaveBeenCalled();
    errSpy.mockRestore();
  });

  it('does not update state after unmount (avoids setState-on-unmounted)', async () => {
    let resolveJson;
    global.fetch.mockReturnValue(new Promise((resolve) => { resolveJson = resolve; }));
    const { result, unmount } = renderHook(() => useVoices());
    unmount();
    await act(async () => {
      resolveJson({ ok: true, json: async () => ({ voices: [{ name: 'late' }] }) });
    });
    expect(result.current.voices).toEqual([]);
  });
});
