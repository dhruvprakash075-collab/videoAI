export default function ToggleRow({ title, description, value, onChange, ariaLabel }) {
  return (
    <div className="flex items-center justify-between p-4 border border-zinc-800/50 rounded-2xl bg-zinc-950/30">
      <div>
        <div className="font-medium text-sm">{title}</div>
        <div className="text-xs text-zinc-500 mt-1 max-w-[200px]">{description}</div>
      </div>
      <button
        type="button"
        onClick={() => onChange(!value)}
        aria-label={ariaLabel ?? `${title}: ${value ? 'on' : 'off'}`}
        aria-pressed={value}
        className={`w-12 h-6 rounded-full transition-colors relative ${
          value ? 'bg-zinc-200' : 'bg-zinc-800'
        }`}
      >
        <span
          className={`w-4 h-4 rounded-full bg-[#0a0a0c] absolute top-1 transition-all ${
            value ? 'left-7' : 'left-1'
          }`}
        />
      </button>
    </div>
  );
}
