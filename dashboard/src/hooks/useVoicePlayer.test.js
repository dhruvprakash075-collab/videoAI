import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import useVoicePlayer from './useVoicePlayer.js';

function makeAudio() {
  const a = {
    paused: false,
    src: '',
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    onended: null,
    onerror: null,
  };
  return a;
}

describe('useVoicePlayer', () => {
  let lastAudio;
  beforeEach(() => {
    lastAudio = makeAudio();
    global.Audio = vi.fn(() => lastAudio);
  });

  it('starts with no playing voice', () => {
    const { result } = renderHook(() => useVoicePlayer());
    expect(result.current.playingVoice).toBeNull();
  });

  it('creates a new Audio element when play() is called', () => {
    const { result } = renderHook(() => useVoicePlayer());
    act(() => { result.current.play('alice'); });
    expect(global.Audio).toHaveBeenCalledWith('/api/audio/preview/alice');
    expect(lastAudio.play).toHaveBeenCalled();
    expect(result.current.playingVoice).toBe('alice');
  });

  it('encodes the voice name in the URL', () => {
    const { result } = renderHook(() => useVoicePlayer());
    act(() => { result.current.play('voice with spaces'); });
    expect(global.Audio).toHaveBeenCalledWith('/api/audio/preview/voice%20with%20spaces');
  });

  it('toggles off when the same voice is played again', () => {
    const { result } = renderHook(() => useVoicePlayer());
    act(() => { result.current.play('alice'); });
    act(() => { result.current.play('alice'); });
    expect(result.current.playingVoice).toBeNull();
  });

  it('stops the previous audio before starting a new one', () => {
    const first = makeAudio();
    const second = makeAudio();
    global.Audio = vi.fn()
      .mockReturnValueOnce(first)
      .mockReturnValueOnce(second);
    const { result } = renderHook(() => useVoicePlayer());
    act(() => { result.current.play('a'); });
    act(() => { result.current.play('b'); });
    expect(first.pause).toHaveBeenCalled();
    expect(second.play).toHaveBeenCalled();
    expect(result.current.playingVoice).toBe('b');
  });

  it('clears playingVoice on audio.onended', () => {
    const { result } = renderHook(() => useVoicePlayer());
    act(() => { result.current.play('alice'); });
    expect(result.current.playingVoice).toBe('alice');
    act(() => { lastAudio.onended(); });
    expect(result.current.playingVoice).toBeNull();
  });

  it('clears playingVoice on audio.onerror', () => {
    const { result } = renderHook(() => useVoicePlayer());
    act(() => { result.current.play('alice'); });
    act(() => { lastAudio.onerror(); });
    expect(result.current.playingVoice).toBeNull();
  });

  it('clears playingVoice when play() rejects', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    lastAudio.play.mockRejectedValue(new Error('autoplay blocked'));
    const { result } = renderHook(() => useVoicePlayer());
    act(() => { result.current.play('alice'); });
    await act(async () => { await Promise.resolve(); });
    expect(result.current.playingVoice).toBeNull();
    errSpy.mockRestore();
  });

  it('pauses and clears the audio ref on unmount', () => {
    const { result, unmount } = renderHook(() => useVoicePlayer());
    act(() => { result.current.play('alice'); });
    unmount();
    expect(lastAudio.pause).toHaveBeenCalled();
  });
});
