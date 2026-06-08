import { Check } from 'lucide-react';

const ACCENTS = {
  a: {
    label: 'Output A',
    bar: 'bg-emerald-500/20',
    text: 'text-emerald-500',
    commitBg: 'bg-emerald-500/10',
    commitText: 'text-emerald-400',
    commitHover: 'hover:bg-emerald-500',
  },
  b: {
    label: 'Output B',
    bar: 'bg-blue-500/20',
    text: 'text-blue-500',
    commitBg: 'bg-blue-500/10',
    commitText: 'text-blue-400',
    commitHover: 'hover:bg-blue-500',
  },
};

export default function VariantPanel({ id, images, onCommit }) {
  const accent = ACCENTS[id];
  return (
    <div className="bg-[#0f0f13]/50 border border-zinc-800/50 rounded-3xl p-6 flex flex-col group relative overflow-hidden">
      <div className={`absolute top-0 left-0 w-full h-1 ${accent.bar}`} />
      <div className="flex justify-between items-center mb-6">
        <span className={`font-mono ${accent.text} text-xs tracking-wider uppercase`}>
          {accent.label}
        </span>
        <button
          type="button"
          onClick={() => onCommit(id)}
          className={`text-xs ${accent.commitBg} ${accent.commitText} ${accent.commitHover} hover:text-white px-4 py-2 rounded-full flex items-center gap-1.5 transition-all`}
        >
          <Check size={14} /> Commit {id.toUpperCase()}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3 flex-1">
        {images.map((src, i) => (
          <div key={src || `${id}-${i}`} className="rounded-xl overflow-hidden border border-zinc-800/50 bg-zinc-900/50 flex items-center justify-center">
            {src ? (
              <img
                src={src}
                alt={`Variant ${id.toUpperCase()} image ${i + 1}`}
                className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-700"
              />
            ) : (
              <span className="text-zinc-600 text-xs">No image</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
