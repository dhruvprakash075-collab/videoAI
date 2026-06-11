import { describe, it, expect, vi, beforeEach } from 'vitest';
import { apiGet, apiSend, API_BASE } from './api.js';

describe('api helpers', () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  describe('API_BASE', () => {
    it('is the empty string (proxied by Vite)', () => {
      expect(API_BASE).toBe('');
    });
  });

  describe('apiGet', () => {
    it('returns parsed JSON on a 2xx response', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ hello: 'world' }),
      });
      const result = await apiGet('/api/foo');
      expect(global.fetch).toHaveBeenCalledWith('/api/foo');
      expect(result).toEqual({ hello: 'world' });
    });

    it('throws on a non-ok response with the path and status in the message', async () => {
      global.fetch.mockResolvedValueOnce({ ok: false, status: 500 });
      await expect(apiGet('/api/bar')).rejects.toThrow('/api/bar -> 500');
    });

    it('propagates network errors as thrown', async () => {
      global.fetch.mockRejectedValueOnce(new Error('network down'));
      await expect(apiGet('/api/x')).rejects.toThrow('network down');
    });
  });

  describe('apiSend', () => {
    it('POSTs the body and returns the raw response', async () => {
      const body = new FormData();
      body.append('a', '1');
      const fakeRes = { ok: true, status: 200 };
      global.fetch.mockResolvedValueOnce(fakeRes);

      const result = await apiSend('/api/upload', body);

      expect(global.fetch).toHaveBeenCalledWith('/api/upload', { method: 'POST', body });
      expect(result).toBe(fakeRes);
    });

    it('uses the supplied method', async () => {
      global.fetch.mockResolvedValueOnce({ ok: true });
      await apiSend('/api/x', 'payload', 'PUT');
      expect(global.fetch).toHaveBeenCalledWith('/api/x', { method: 'PUT', body: 'payload' });
    });

    it('returns the raw response so callers can check .ok', async () => {
      global.fetch.mockResolvedValueOnce({ ok: false, status: 400 });
      const res = await apiSend('/api/y', new FormData());
      expect(res.ok).toBe(false);
    });
  });
});
