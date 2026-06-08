import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import useScriptUpload from './useScriptUpload.js';

const okJson = (data) => ({ ok: true, json: async () => data });

function makeFile(name, content = 'x') {
  return new File([content], name, { type: 'text/plain' });
}

async function uploadFile(result, file) {
  await act(async () => { await result.current.upload(file); });
}

describe('useScriptUpload', () => {
  beforeEach(() => {
    global.fetch = vi.fn();
    global.alert = vi.fn();
  });

  it('returns a ref + an upload function', () => {
    global.fetch.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useScriptUpload());
    expect(result.current.inputRef).toBeDefined();
    expect(result.current.inputRef.current).toBeNull();
    expect(typeof result.current.upload).toBe('function');
  });

  it('POSTs the file to /api/upload_script with topic derived from filename', async () => {
    global.fetch.mockResolvedValue(okJson({}));
    const { result } = renderHook(() => useScriptUpload());
    const file = makeFile('mytopic.txt');
    await uploadFile(result, file);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = global.fetch.mock.calls[0];
    expect(url).toBe('/api/upload_script');
    expect(opts.method).toBe('POST');
    const form = opts.body;
    expect(form.get('file')).toBe(file);
    expect(form.get('topic')).toBe('mytopic');
  });

  it('surfaces a backend error message via alert on non-ok response', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      json: async () => ({ message: 'pipeline already running' }),
    });
    const { result } = renderHook(() => useScriptUpload());
    await uploadFile(result, makeFile('topic.txt'));
    expect(global.alert).toHaveBeenCalledWith('Upload failed: pipeline already running');
  });

  it('falls back to statusText when the error body is unparseable', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => { throw new Error('not json'); },
    });
    const { result } = renderHook(() => useScriptUpload());
    await uploadFile(result, makeFile('topic.txt'));
    expect(global.alert).toHaveBeenCalledWith('Upload failed: Internal Server Error');
  });

  it('surfaces network errors via alert', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    global.fetch.mockRejectedValue(new Error('offline'));
    const { result } = renderHook(() => useScriptUpload());
    await uploadFile(result, makeFile('topic.txt'));
    expect(global.alert).toHaveBeenCalledWith('Upload error: offline');
    errSpy.mockRestore();
  });

  it('does nothing when called with a falsy file', async () => {
    global.fetch.mockResolvedValue({ ok: true });
    const { result } = renderHook(() => useScriptUpload());
    await uploadFile(result, null);
    await uploadFile(result, undefined);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('resets the file input value after upload', async () => {
    global.fetch.mockResolvedValue(okJson({}));
    const { result } = renderHook(() => useScriptUpload());
    const ref = result.current.inputRef;
    ref.current = { value: '/some/path/topic.txt' };
    await uploadFile(result, makeFile('topic.txt'));
    expect(ref.current.value).toBe('');
  });

  it('handles a .TXT extension case-insensitively when deriving the topic', async () => {
    global.fetch.mockResolvedValue(okJson({}));
    const { result } = renderHook(() => useScriptUpload());
    await uploadFile(result, makeFile('MyTopic.TXT'));
    const form = global.fetch.mock.calls[0][1].body;
    expect(form.get('topic')).toBe('MyTopic');
  });
});
