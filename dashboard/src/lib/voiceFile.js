const ALLOWED_TYPES = ['audio/wav', 'audio/wave', 'audio/x-wav', 'audio/mpeg', 'audio/mp3'];
const ALLOWED_EXTS = ['.wav', '.mp3'];
const MAX_SIZE_BYTES = 10 * 1024 * 1024;

export function validateVoiceFile(file) {
  if (!file) return 'No file provided.';
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  if (!ALLOWED_TYPES.includes(file.type) && !ALLOWED_EXTS.includes(ext)) {
    return 'Only WAV or MP3 files are accepted.';
  }
  if (file.size > MAX_SIZE_BYTES) {
    return 'File is too large. Maximum size is 10 MB.';
  }
  return null;
}
