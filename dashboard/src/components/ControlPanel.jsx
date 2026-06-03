import { useEffect, useRef, useState } from 'react';
import { X, Settings2, Sliders, Type, Mic2 } from 'lucide-react';
import { API_BASE } from '../lib/api.js';
import ToggleRow from './ToggleRow.jsx';

const VOICE_ENGINES = [
  { id: 'omnivoice', label: 'OmniVoice (Default)', description: 'Ultra-expressive local cloning.' },
  { id: 'edge',      label: 'Edge TTS',           description: 'Fast, reliable cloud voices.' },
];

const DEFAULT_CONFIG = {
  voiceEngine: 'omnivoice',
  dynamicSubtitles: true,
  uncappedScaling: false,
  maxImagesPerSegment: 6,
};

export default function ControlPanel({ onClose }) {
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [saving, setSaving] = useState(false);
  const abortRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    fetch(`${API_BASE}/api/config`, { signal: controller.signal })
      .then((res) => res.json())
      .then((data) => {
        if (data && !data.status) setConfig(data);
      })
      .catch((err) => {
        if (err.name !== 'AbortError') console.error('Failed to load configuration:', err);
      });
    return () => controller.abort();
  }, []);

  const update = (patch) => setConfig((prev) => ({ ...prev, ...patch }));

  const handleSave = async () => {
    setSaving(true);
    try {
      const formData = new FormData();
      formData.append('voice_engine', config.voiceEngine);
      formData.append('dynamic_subtitles', String(config.dynamicSubtitles));
      formData.append('uncapped_scaling', String(config.uncappedScaling));
      formData.append('max_images_per_segment', config.maxImagesPerSegment);

      const res = await fetch(`${API_BASE}/api/config`, { method: 'POST', body: formData });
      const result = await res.json();
      if (result.status === 'success') {
        onClose();
      } else {
        alert(`Failed to save settings: ${result.message}`);
      }
    } catch (err) {
      console.error('Save failed:', err);
      alert(`Save failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#0a0a0c] text-zinc-200">
      <PanelHeader onClose={onClose} />

      <div className="p-8 flex-1 overflow-y-auto flex flex-col gap-10">
        <Section icon={Mic2} title="Voice Engine">
          <div className="grid grid-cols-1 gap-2">
            {VOICE_ENGINES.map((engine) => (
              <EngineButton
                key={engine.id}
                engine={engine}
                active={config.voiceEngine === engine.id}
                onClick={() => update({ voiceEngine: engine.id })}
              />
            ))}
          </div>
        </Section>

        <Section icon={Sliders} title="Visual Generation">
          <ToggleRow
            title="Uncapped Scaling"
            description="Allow Director AI to dictate exact number of images per segment."
            value={config.uncappedScaling}
            onChange={(v) => update({ uncappedScaling: v })}
          />
          {!config.uncappedScaling && <ImagesSlider config={config} onChange={update} />}
        </Section>

        <Section icon={Type} title="Post-Production">
          <ToggleRow
            title="TikTok-Style Subtitles"
            description="Dynamically burn animated subtitles onto final video."
            value={config.dynamicSubtitles}
            onChange={(v) => update({ dynamicSubtitles: v })}
          />
        </Section>
      </div>

      <div className="p-8 border-t border-zinc-800/50">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="w-full py-4 rounded-2xl bg-white text-black font-medium tracking-wide hover:bg-zinc-200 transition-colors disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save Configuration'}
        </button>
      </div>
    </div>
  );
}

function PanelHeader({ onClose }) {
  return (
    <div className="px-8 py-6 border-b border-zinc-800/50 flex items-center justify-between">
      <h2 className="text-sm font-medium tracking-widest uppercase flex items-center gap-3">
        <Settings2 size={16} className="text-zinc-500" /> System Config
      </h2>
      <button
        type="button"
        onClick={onClose}
        className="text-zinc-600 hover:text-white transition-colors"
        aria-label="Close settings"
      >
        <X size={20} strokeWidth={1.5} />
      </button>
    </div>
  );
}

function Section({ icon: Icon, title, children }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-zinc-400 mb-2">
        <Icon size={16} />
        <h3 className="text-xs font-medium uppercase tracking-wider">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function EngineButton({ engine, active, onClick }) {
  const tone = active
    ? 'border-zinc-500 bg-zinc-900 text-white'
    : 'border-zinc-800/50 bg-zinc-950/30 text-zinc-500 hover:border-zinc-700 hover:text-zinc-300';
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`p-4 text-left border rounded-2xl transition-all duration-300 ${tone}`}
    >
      <div className="font-medium capitalize">{engine.label}</div>
      <div className="text-xs mt-1 opacity-60">{engine.description}</div>
    </button>
  );
}

function ImagesSlider({ config, onChange }) {
  return (
    <div className="p-4 border border-zinc-800/50 rounded-2xl bg-zinc-950/30">
      <div className="flex justify-between text-sm mb-4">
        <span className="font-medium">Images Per Segment</span>
        <span className="text-zinc-400">{config.maxImagesPerSegment}</span>
      </div>
      <input
        type="range"
        min="1"
        max="15"
        value={config.maxImagesPerSegment}
        aria-label="Images per segment"
        onChange={(e) => onChange({ maxImagesPerSegment: parseInt(e.target.value, 10) })}
        className="w-full accent-zinc-500"
      />
    </div>
  );
}
