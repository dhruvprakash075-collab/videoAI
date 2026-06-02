MISSING VOICE SAMPLES — Required for Voice Cloning (XTTS)
==========================================================

The following files are referenced in config.yaml but are missing:

  - lotm_narrator_sample.wav  (used for Lumian Lee's voice)
  - lotm_sample_short.wav     (used for Klein Moretti)
  - kiana_sample.wav          (used for Kiana Kaslana)

Format: 5-30 second WAV file, 22050Hz mono, clean speech sample of the character's voice.

Without these, the TTS engine will use its default voice (no voice cloning).
Place .wav files here and the pipeline will auto-detect them.

To create a voice sample:
  1. Record 5-30 seconds of clear speech in the target voice style
  2. Convert to WAV: ffmpeg -i input.mp3 -ac 1 -ar 22050 -t 30 output.wav
  3. Place the .wav in this directory
