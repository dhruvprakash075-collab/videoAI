import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api.js';
import { Loader, BrainCircuit, Globe, Eye, Star, Users } from 'lucide-react';

const TYPE_ICONS = {
  character: Users,
  world_lore: Globe,
  visual_lock: Eye,
  motif: Star,
};

const TYPE_COLORS = {
  character: 'text-blue-400',
  world_lore: 'text-emerald-400',
  visual_lock: 'text-purple-400',
  motif: 'text-amber-400',
};

export default function MemoryPanel() {
  const [memory, setMemory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    setLoading(true);
    apiGet('/api/memory')
      .then((data) => setMemory(data.memory || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const types = ['all', ...new Set(memory.map((m) => m.type || 'other'))];
  const filtered = filter === 'all' ? memory : memory.filter((m) => (m.type || 'other') === filter);
  const grouped = {};
  filtered.forEach((m) => {
    const scope = m.scope || m.project || 'general';
    if (!grouped[scope]) grouped[scope] = [];
    grouped[scope].push(m);
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader size={20} className="animate-spin text-zinc-500" />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto h-full flex flex-col animate-in fade-in duration-500">
      <header className="mb-6">
        <h2 className="text-2xl font-light tracking-tight text-white">Memory</h2>
        <p className="text-zinc-500 text-sm">Project and story memory — read-only view.</p>
      </header>

      <div className="flex gap-2 mb-4 flex-wrap">
        {types.map((t) => (
          <button
            key={t}
            onClick={() => setFilter(t)}
            className={`px-3 py-1 rounded-lg text-xs transition-colors ${
              filter === t
                ? 'bg-zinc-700 text-white'
                : 'bg-zinc-800/40 text-zinc-500 hover:text-zinc-300'
            }`}
          >
            {t === 'all' ? 'All' : t.replace('_', ' ')}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto space-y-4">
        {Object.entries(grouped).length === 0 && (
          <div className="text-center text-zinc-600 mt-12">
            <BrainCircuit size={32} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">No memory items found.</p>
          </div>
        )}
        {Object.entries(grouped).map(([scope, items]) => (
          <div key={scope}>
            <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">{scope}</h3>
            <div className="space-y-1">
              {items.map((item, i) => {
                const Icon = TYPE_ICONS[item.type] || BrainCircuit;
                const color = TYPE_COLORS[item.type] || 'text-zinc-400';
                return (
                  <div key={i} className="flex items-center gap-3 p-2.5 rounded-lg bg-zinc-900/30 border border-zinc-800/40">
                    <Icon size={14} className={`shrink-0 ${color}`} />
                    <div className="flex-1 min-w-0">
                      <div className="text-sm text-zinc-200 truncate">{item.name || item.key}</div>
                      {item.importance && (
                        <div className="text-[10px] text-zinc-500">Importance: {item.importance}</div>
                      )}
                    </div>
                    {item.type && (
                      <span className="text-[10px] text-zinc-600 capitalize">{item.type.replace('_', ' ')}</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
