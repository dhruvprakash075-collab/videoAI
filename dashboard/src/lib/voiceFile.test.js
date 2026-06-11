import { describe, it, expect } from 'vitest';
import { validateVoiceFile } from './voiceFile.js';

function makeFile({ name = 'sample.wav', type = 'audio/wav', size = 1024 } = {}) {
  return { name, type, size };
}

describe('validateVoiceFile', () => {
  describe('rejects', () => {
    it('null/undefined', () => {
      expect(validateVoiceFile(null)).toBeTruthy();
      expect(validateVoiceFile(undefined)).toBeTruthy();
    });

    it('disallowed MIME types', () => {
      const err = validateVoiceFile(makeFile({ name: 'song.flac', type: 'audio/flac' }));
      expect(err).toMatch(/WAV or MP3/);
    });

    it('disallowed extensions', () => {
      const err = validateVoiceFile(makeFile({ name: 'song.flac', type: '' }));
      expect(err).toMatch(/WAV or MP3/);
    });

    it('files larger than 10 MB', () => {
      const err = validateVoiceFile(makeFile({ size: 11 * 1024 * 1024 }));
      expect(err).toMatch(/too large/);
    });
  });

  describe('accepts', () => {
    it('WAV with correct MIME', () => {
      expect(validateVoiceFile(makeFile({ name: 'a.wav', type: 'audio/wav' }))).toBeNull();
    });

    it('WAV with audio/wave MIME', () => {
      expect(validateVoiceFile(makeFile({ name: 'a.wav', type: 'audio/wave' }))).toBeNull();
    });

    it('WAV with audio/x-wav MIME', () => {
      expect(validateVoiceFile(makeFile({ name: 'a.wav', type: 'audio/x-wav' }))).toBeNull();
    });

    it('MP3 with audio/mpeg MIME', () => {
      expect(validateVoiceFile(makeFile({ name: 'a.mp3', type: 'audio/mpeg' }))).toBeNull();
    });

    it('MP3 with audio/mp3 MIME', () => {
      expect(validateVoiceFile(makeFile({ name: 'a.mp3', type: 'audio/mp3' }))).toBeNull();
    });

    it('uppercase extension is normalized', () => {
      expect(validateVoiceFile(makeFile({ name: 'A.WAV', type: '' }))).toBeNull();
    });

    it('file with empty type but valid extension', () => {
      expect(validateVoiceFile(makeFile({ name: 'no-type.wav', type: '' }))).toBeNull();
    });

    it('file at exactly 10 MB', () => {
      expect(validateVoiceFile(makeFile({ size: 10 * 1024 * 1024 }))).toBeNull();
    });
  });
});
