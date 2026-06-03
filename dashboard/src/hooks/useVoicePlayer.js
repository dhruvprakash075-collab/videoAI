import { useEffect, useRef, useState } from 'react';

export default function useVoicePlayer() {
  const audioRef = useRef(null);
  const [playingVoice, setPlayingVoice] = useState(null);

  useEffect(() => () => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
  }, []);

  const play = (voiceName) => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (playingVoice === voiceName) {
      setPlayingVoice(null);
      return;
    }
    const audio = new Audio(`/api/audio/preview/${encodeURIComponent(voiceName)}`);
    audioRef.current = audio;
    setPlayingVoice(voiceName);
    audio.play().catch((err) => {
      console.error('Voice preview failed:', err);
      setPlayingVoice(null);
    });
    audio.onended = () => {
      setPlayingVoice(null);
      audioRef.current = null;
    };
    audio.onerror = () => {
      setPlayingVoice(null);
      audioRef.current = null;
    };
  };

  return { playingVoice, play };
}
