import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api.js';
import { Loader, Users, Hash, Image, CheckCircle, XCircle } from 'lucide-react';

export default function CharactersPanel() {
  const [characters, setCharacters] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    apiGet('/api/characters')
      .then((data) => setCharacters(data.characters || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader size={20} className="animate-spin text-zinc-500" />
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto h-full flex flex-col animate-in fade-in duration-500">
      <header className="mb-6">
        <h2 className="text-2xl font-light tracking-tight text-white">Characters</h2>
        <p className="text-zinc-500 text-sm">Character assets from all projects — read-only.</p>
      </header>

      <div className="flex-1 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-4">
        {characters.length === 0 && (
          <div className="col-span-full text-center text-zinc-600 mt-16">
            <Users size={32} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">No character assets found.</p>
          </div>
        )}
        {characters.map((char, i) => (
          <div key={i} className="bg-zinc-900/30 border border-zinc-800/50 rounded-2xl p-4">
            <div className="flex gap-4">
              {char.master_portrait && (
                <img
                  src={char.master_portrait}
                  alt={char.name}
                  className="w-20 h-20 rounded-xl object-cover bg-zinc-800"
                />
              )}
              <div className="flex-1 min-w-0">
                <h3 className="text-base font-medium text-white truncate">{char.name}</h3>
                <p className="text-xs text-zinc-500">Project: {char.project}</p>
                <div className="flex gap-3 mt-2">
                  {char.identity_hash && (
                    <span className="flex items-center gap-1 text-[10px] text-zinc-500">
                      <Hash size={10} /> {char.identity_hash.slice(0, 8)}...
                    </span>
                  )}
                  <span className="flex items-center gap-1 text-[10px] text-green-500">
                    <CheckCircle size={10} /> {char.approved_count || 0}
                  </span>
                  <span className="flex items-center gap-1 text-[10px] text-red-500">
                    <XCircle size={10} /> {char.rejected_count || 0}
                  </span>
                </div>
              </div>
            </div>

            <div className="mt-3 flex gap-2 flex-wrap">
              {char.full_body_ref && (
                <a
                  href={char.full_body_ref}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1 text-[10px] px-2 py-1 rounded bg-zinc-800 text-zinc-400 hover:text-white"
                >
                  <Image size={10} /> Full Body
                </a>
              )}
              {char.ip_adapter_refs && char.ip_adapter_refs.length > 0 && (
                <span className="text-[10px] px-2 py-1 rounded bg-zinc-800 text-zinc-400">
                  IPAdapter: {char.ip_adapter_refs.length}
                </span>
              )}
              {char.lora_candidates && char.lora_candidates.length > 0 && (
                <span className="text-[10px] px-2 py-1 rounded bg-zinc-800 text-zinc-400">
                  LoRA: {char.lora_candidates.length}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
