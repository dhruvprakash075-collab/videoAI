const TAB_TITLES = {
  preview: 'Director Canvas',
  voices: 'Voice Studio',
  'ab-testing': 'A/B Testing',
};

const STATE_DOT = {
  running: 'bg-emerald-500 animate-pulse',
  paused: 'bg-amber-500',
  error: 'bg-red-500',
};

export default function Header({ activeTab, status, onPause }) {
  const title = TAB_TITLES[activeTab] ?? '';
  const dotClass = STATE_DOT[status.state] ?? 'bg-zinc-600';

  return (
    <header className="h-16 flex items-center px-8 justify-between z-10 border-b border-zinc-800/20">
      <h1 className="text-sm font-medium text-zinc-400 uppercase tracking-widest">{title}</h1>
      <div className="flex items-center gap-4">
        {status.state === 'running' && (
          <button
            type="button"
            onClick={onPause}
            className="text-xs font-medium px-4 py-1.5 bg-zinc-900 border border-zinc-800 rounded-full text-zinc-300 hover:bg-zinc-800 transition-colors"
          >
            Pause Engine
          </button>
        )}
        <span className="flex items-center gap-2 text-[10px] uppercase font-mono px-3 py-1.5 bg-zinc-900/50 border border-zinc-800/50 rounded-full text-zinc-400">
          <span className={`w-1.5 h-1.5 block rounded-full ${dotClass}`} />
          {status.state}
        </span>
      </div>
    </header>
  );
}
