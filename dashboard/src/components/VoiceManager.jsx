import { useState } from 'react';
import UploadZone from './UploadZone.jsx';
import VoiceCard from './VoiceCard.jsx';
import useVoices from '../hooks/useVoices.js';
import useVoicePlayer from '../hooks/useVoicePlayer.js';

export default function VoiceManager() {
  const [characterName, setCharacterName] = useState('');
  const { voices, refresh } = useVoices();
  const { playingVoice, play } = useVoicePlayer();

  return (
    <div className="max-w-5xl mx-auto h-full flex flex-col pt-8 animate-in fade-in duration-500">
      <header className="mb-12 text-center">
        <h2 className="text-3xl font-light tracking-tight text-white mb-3">Voice Studio</h2>
        <p className="text-zinc-400 font-light">Manage and upload character reference voices.</p>
      </header>

      <div className="flex gap-8">
        <div className="flex-1">
          <UploadZone
            characterName={characterName}
            onCharacterNameChange={setCharacterName}
            onUploaded={refresh}
          />
        </div>

        <div className="flex-[1.5]">
          <VoiceGallery voices={voices} playingVoice={playingVoice} onPlay={play} />
        </div>
      </div>
    </div>
  );
}

function VoiceGallery({ voices, playingVoice, onPlay }) {
  return (
    <div className="bg-zinc-900/50 border border-zinc-800/50 p-6 rounded-3xl backdrop-blur-sm h-full">
      <h3 className="text-lg font-medium text-white mb-6">Voice Library</h3>
      {voices.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 text-zinc-600">
          <p>No voices uploaded yet.</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4">
          {voices.map((voice) => (
            <VoiceCard
              key={voice.name}
              voice={voice}
              isPlaying={playingVoice === voice.name}
              onPlay={onPlay}
            />
          ))}
        </div>
      )}
    </div>
  );
}
