import { AlignLeft } from 'lucide-react';
import { API_BASE } from '../lib/api.js';

export default function PreviewCanvas({ video, scriptInputRef, onScriptPicked }) {
  return (
    <div className="max-w-4xl mx-auto h-full flex flex-col items-center justify-center animate-in fade-in duration-700">
      {video ? (
        <video
          src={`${API_BASE}${video}`}
          controls
          className="w-full max-h-[65vh] rounded-xl shadow-2xl bg-black ring-1 ring-zinc-800"
        />
      ) : (
        <UploadCard scriptInputRef={scriptInputRef} onScriptPicked={onScriptPicked} />
      )}
    </div>
  );
}

function UploadCard({ scriptInputRef, onScriptPicked }) {
  return (
    <div className="w-full aspect-video rounded-3xl border border-zinc-800/50 bg-zinc-900/20 flex flex-col items-center justify-center text-zinc-500 relative overflow-hidden">
      <AlignLeft size={32} className="mb-4 opacity-30" strokeWidth={1.5} />
      <p className="font-light tracking-wide text-zinc-400">Upload Lore Script</p>
      <p className="text-xs mt-2 opacity-50 font-mono">.txt .md (text files only)</p>
      <input
        ref={scriptInputRef}
        type="file"
        accept=".txt,.md"
        onChange={(e) => onScriptPicked(e.target.files?.[0])}
        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
      />
    </div>
  );
}
