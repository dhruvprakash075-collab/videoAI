import { Play } from 'lucide-react';

export default function VoiceCard({ voice, isPlaying, onPlay }) {
  return (
    <div className="group relative bg-zinc-950/50 border border-zinc-800 rounded-2xl p-4 hover:border-zinc-700 transition-all cursor-pointer overflow-hidden">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={() => onPlay(voice.name)}
          aria-label={`Preview voice ${voice.name}`}
          className="w-10 h-10 rounded-full bg-zinc-900 text-zinc-400 flex items-center justify-center group-hover:bg-emerald-500/10 group-hover:text-emerald-500 transition-colors"
        >
          {isPlaying ? (
            <span className="w-3 h-3 rounded-sm bg-emerald-500 block" />
          ) : (
            <Play size={16} className="ml-0.5" />
          )}
        </button>
        <div>
          <p className="text-zinc-200 font-medium truncate w-32">{voice.name}</p>
          <p className="text-xs text-zinc-600 mt-1">{(voice.size / 1024).toFixed(1)} KB</p>
        </div>
      </div>
    </div>
  );
}
