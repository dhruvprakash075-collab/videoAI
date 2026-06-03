import { useState } from 'react';
import { RefreshCw, Zap } from 'lucide-react';
import VariantPanel from './VariantPanel.jsx';
import useABJob from '../hooks/useABJob.js';

const SEGMENT_NUM = 1;
const DEFAULT_PROMPT_A = 'A futuristic city at night, neon lights, cinematic';
const DEFAULT_PROMPT_B = 'A futuristic city at night, raining, highly detailed, photorealistic';

export default function ABPlayground() {
  const [promptA, setPromptA] = useState(DEFAULT_PROMPT_A);
  const [promptB, setPromptB] = useState(DEFAULT_PROMPT_B);
  const { status, images, start, pick } = useABJob();

  const isRunning = status === 'running' || status === 'starting';
  const hasResults = images.a.length > 0 || images.b.length > 0;

  return (
    <div className="max-w-6xl mx-auto h-full flex flex-col pt-8 animate-in fade-in duration-500">
      <header className="mb-10 text-center">
        <h2 className="text-3xl font-light tracking-tight text-white mb-2">A/B Testing Studio</h2>
        <p className="text-zinc-500 font-light text-sm">Visually compare prompts and face-lock accuracy.</p>
      </header>

      <section className="bg-[#0f0f13] border border-zinc-800/50 rounded-3xl p-8 mb-8 shadow-2xl">
        <div className="grid grid-cols-2 gap-8">
          <PromptField
            label="Variant A"
            accent="emerald"
            value={promptA}
            onChange={setPromptA}
          />
          <PromptField
            label="Variant B"
            accent="blue"
            value={promptB}
            onChange={setPromptB}
          />
        </div>
        <div className="mt-8 flex justify-center">
          <button
            type="button"
            onClick={() => start(SEGMENT_NUM, promptA, promptB)}
            disabled={isRunning}
            className="bg-white hover:bg-zinc-200 text-black font-medium px-8 py-3 rounded-full flex items-center gap-2 transition-colors disabled:opacity-50"
          >
            {isRunning ? <RefreshCw size={16} className="animate-spin" /> : <Zap size={16} />}
            {isRunning ? 'Generating Images...' : 'Run A/B Comparison'}
          </button>
        </div>
      </section>

      {hasResults && (
        <section className="flex-1 grid grid-cols-2 gap-8 pb-8 animate-in slide-in-from-bottom-8 duration-700">
          <VariantPanel id="a" images={images.a} onCommit={(choice) => pick(choice, SEGMENT_NUM)} />
          <VariantPanel id="b" images={images.b} onCommit={(choice) => pick(choice, SEGMENT_NUM)} />
        </section>
      )}
    </div>
  );
}

function PromptField({ label, accent, value, onChange }) {
  return (
    <div>
      <label className={`block text-xs font-mono text-${accent}-500 uppercase tracking-wider mb-3`}>
        {label}
      </label>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={2}
        className={`w-full bg-zinc-950/50 border border-zinc-800/50 rounded-2xl p-4 text-sm text-zinc-300 focus:outline-none focus:ring-1 focus:ring-${accent}-500/50 transition-all resize-none font-light leading-relaxed`}
      />
    </div>
  );
}
