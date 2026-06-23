import { useEffect, useRef, useState } from 'react';
import { Folder, Gauge, Server, Settings2, Sliders, Type, Mic2, Workflow, X } from 'lucide-react';
import { API_BASE } from '../lib/api.js';
import ToggleRow from './ToggleRow.jsx';

const VOICE_ENGINES = [
  { id: 'supertonic', label: 'Supertonic 3',       description: 'CPU-only, 31 languages, custom voice JSON.' },
  { id: 'omnivoice', label: 'OmniVoice',           description: 'Ultra-expressive local cloning.' },
];

const DEFAULT_CONFIG = {
  voiceEngine: 'supertonic',
  dynamicSubtitles: true,
  uncappedScaling: false,
  maxImagesPerSegment: 6,
  imageBackend: 'bonsai',
  compositionMode: 'one_pass',
  comfyUiAdvanced: {
    autoStart: true,
    server: 'http://127.0.0.1:8188',
    host: '127.0.0.1',
    port: 8188,
    root: 'external/ComfyUI',
    python: 'external/ComfyUI/.venv/Scripts/python.exe',
    workflowPath: 'config/comfyui/workflows/text_to_image_api.json',
    checkpoint: 'DreamShaper_8_pruned.safetensors',
    width: 1024,
    height: 1024,
    steps: 20,
    cfg: 7.0,
    samplerName: 'euler',
    scheduler: 'normal',
    timeoutSeconds: 300,
    pollSeconds: 1,
    unloadAfterBatch: true,
    openBrowser: false,
    fallbackBackend: 'bonsai',
  },
};

const BACKEND_OPTIONS = [
  { value: 'bonsai', label: 'Bonsai' },
  { value: 'comfyui', label: 'ComfyUI' },
];

const COMPOSITION_OPTIONS = [
  { value: 'one_pass', label: 'One Pass' },
];

const FALLBACK_OPTIONS = [
  { value: 'bonsai', label: 'Bonsai' },
  { value: 'none', label: 'None' },
];

const SAMPLER_OPTIONS = ['euler', 'euler_ancestral', 'dpmpp_2m', 'dpmpp_sde', 'ddim'];
const SCHEDULER_OPTIONS = ['normal', 'karras', 'exponential', 'simple'];

export default function ControlPanel({ onClose }) {
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [activeMenu, setActiveMenu] = useState('general');
  const [saving, setSaving] = useState(false);
  const abortRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
fetch(`${API_BASE}/api/config`, { signal: controller.signal })
      .then((res) => res.json())
      .then((data) => {
        if (data && !data.status) {
          setConfig((prev) => ({
            ...prev,
            ...data,
            compositionMode: data.compositionMode || 'one_pass',
            comfyUiAdvanced: {
              ...DEFAULT_CONFIG.comfyUiAdvanced,
              ...prev.comfyUiAdvanced,
              ...(data.comfyUiAdvanced || {}),
            },
          }));
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') console.error('Failed to load configuration:', err);
      });
    return () => controller.abort();
  }, []);

const update = (patch) => setConfig((prev) => ({ ...prev, ...patch }));
  const updateComfyUi = (patch) =>
    setConfig((prev) => ({
      ...prev,
      comfyUiAdvanced: {
        ...DEFAULT_CONFIG.comfyUiAdvanced,
        ...prev.comfyUiAdvanced,
        ...patch,
      },
    }));

  const handleSave = async () => {
    setSaving(true);
    try {
      const comfyUi = {
        ...DEFAULT_CONFIG.comfyUiAdvanced,
        ...(config.comfyUiAdvanced || {}),
      };
      const formData = new FormData();
formData.append('voice_engine', config.voiceEngine);
      formData.append('dynamic_subtitles', String(config.dynamicSubtitles));
      formData.append('uncapped_scaling', String(config.uncappedScaling));
      formData.append('max_images_per_segment', config.maxImagesPerSegment);
      formData.append('image_backend', config.imageBackend);
      formData.append('composition_mode', config.compositionMode || 'one_pass');
      formData.append('comfyui_auto_start', String(comfyUi.autoStart));
      formData.append('comfyui_server', comfyUi.server);
      formData.append('comfyui_host', comfyUi.host);
      formData.append('comfyui_port', comfyUi.port);
      formData.append('comfyui_root', comfyUi.root);
      formData.append('comfyui_python', comfyUi.python);
      formData.append('comfyui_workflow_path', comfyUi.workflowPath);
      formData.append('comfyui_checkpoint', comfyUi.checkpoint);
      formData.append('comfyui_width', comfyUi.width);
      formData.append('comfyui_height', comfyUi.height);
      formData.append('comfyui_steps', comfyUi.steps);
      formData.append('comfyui_cfg', comfyUi.cfg);
      formData.append('comfyui_sampler_name', comfyUi.samplerName);
      formData.append('comfyui_scheduler', comfyUi.scheduler);
      formData.append('comfyui_timeout_seconds', comfyUi.timeoutSeconds);
      formData.append('comfyui_poll_seconds', comfyUi.pollSeconds);
      formData.append('comfyui_unload_after_batch', String(comfyUi.unloadAfterBatch));
      formData.append('comfyui_open_browser', String(comfyUi.openBrowser));
      formData.append('comfyui_fallback_backend', comfyUi.fallbackBackend);

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
      <SettingsMenu activeMenu={activeMenu} onChange={setActiveMenu} />

<div className="p-8 flex-1 overflow-y-auto flex flex-col gap-10">
        {activeMenu === 'general' ? (
          <GeneralSettings config={config} update={update} />
        ) : (
          <ComfyUiAdvanceSettings
            config={config}
            update={update}
            updateComfyUi={updateComfyUi}
          />
        )}
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

function SettingsMenu({ activeMenu, onChange }) {
  return (
    <div className="px-8 pt-5">
      <div className="grid grid-cols-2 gap-2 rounded-lg border border-zinc-800/60 bg-zinc-950/40 p-1">
        <MenuButton
          active={activeMenu === 'general'}
          label="General"
          onClick={() => onChange('general')}
        />
        <MenuButton
          active={activeMenu === 'comfyui'}
          label="Comfy UI"
          onClick={() => onChange('comfyui')}
        />
      </div>
    </div>
  );
}

function MenuButton({ active, label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`h-9 rounded-md text-xs font-medium transition-colors ${
        active
          ? 'bg-zinc-100 text-zinc-950'
          : 'text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200'
      }`}
    >
      {label}
    </button>
  );
}

function GeneralSettings({ config, update }) {
  return (
    <>
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
        <SelectField
          label="Composition Mode"
          value={config.compositionMode || 'one_pass'}
          options={COMPOSITION_OPTIONS}
          onChange={(value) => update({ compositionMode: value })}
        />
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
    </>
  );
}

function ComfyUiAdvanceSettings({ config, update, updateComfyUi }) {
  const comfyUi = {
    ...DEFAULT_CONFIG.comfyUiAdvanced,
    ...(config.comfyUiAdvanced || {}),
  };

  return (
    <>
      <Section icon={Server} title="Comfy UI Advance">
        <SelectField
          label="Image Backend"
          value={config.imageBackend}
          options={BACKEND_OPTIONS}
          onChange={(value) => update({ imageBackend: value })}
        />
        <ToggleRow
          title="Auto Start"
          description="Start the local ComfyUI server for pipeline jobs."
          value={comfyUi.autoStart}
          onChange={(value) => updateComfyUi({ autoStart: value })}
        />
        <TextField
          label="Server URL"
          value={comfyUi.server}
          onChange={(value) => updateComfyUi({ server: value })}
        />
        <div className="grid grid-cols-2 gap-3">
          <TextField
            label="Host"
            value={comfyUi.host}
            onChange={(value) => updateComfyUi({ host: value })}
          />
          <NumberField
            label="Port"
            min={1}
            value={comfyUi.port}
            onChange={(value) => updateComfyUi({ port: value })}
          />
        </div>
      </Section>

      <Section icon={Folder} title="Runtime Paths">
        <TextField
          label="ComfyUI Root"
          value={comfyUi.root}
          onChange={(value) => updateComfyUi({ root: value })}
        />
        <TextField
          label="Python Executable"
          value={comfyUi.python}
          onChange={(value) => updateComfyUi({ python: value })}
        />
        <TextField
          label="Workflow JSON"
          value={comfyUi.workflowPath}
          onChange={(value) => updateComfyUi({ workflowPath: value })}
        />
        <TextField
          label="Checkpoint"
          value={comfyUi.checkpoint}
          onChange={(value) => updateComfyUi({ checkpoint: value })}
        />
      </Section>

      <Section icon={Workflow} title="Workflow Parameters">
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label="Width"
            min={64}
            step={64}
            value={comfyUi.width}
            onChange={(value) => updateComfyUi({ width: value })}
          />
          <NumberField
            label="Height"
            min={64}
            step={64}
            value={comfyUi.height}
            onChange={(value) => updateComfyUi({ height: value })}
          />
          <NumberField
            label="Steps"
            min={1}
            value={comfyUi.steps}
            onChange={(value) => updateComfyUi({ steps: value })}
          />
          <NumberField
            label="CFG"
            min={0}
            step={0.5}
            value={comfyUi.cfg}
            onChange={(value) => updateComfyUi({ cfg: value })}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <SelectField
            label="Sampler"
            value={comfyUi.samplerName}
            options={SAMPLER_OPTIONS.map((value) => ({ value, label: value }))}
            onChange={(value) => updateComfyUi({ samplerName: value })}
          />
          <SelectField
            label="Scheduler"
            value={comfyUi.scheduler}
            options={SCHEDULER_OPTIONS.map((value) => ({ value, label: value }))}
            onChange={(value) => updateComfyUi({ scheduler: value })}
          />
        </div>
      </Section>

      <Section icon={Gauge} title="Execution">
        <div className="grid grid-cols-2 gap-3">
          <NumberField
            label="Timeout Seconds"
            min={1}
            value={comfyUi.timeoutSeconds}
            onChange={(value) => updateComfyUi({ timeoutSeconds: value })}
          />
          <NumberField
            label="Poll Seconds"
            min={0.1}
            step={0.1}
            value={comfyUi.pollSeconds}
            onChange={(value) => updateComfyUi({ pollSeconds: value })}
          />
        </div>
        <SelectField
          label="Fallback Backend"
          value={comfyUi.fallbackBackend}
          options={FALLBACK_OPTIONS}
          onChange={(value) => updateComfyUi({ fallbackBackend: value })}
        />
        <ToggleRow
          title="Unload After Batch"
          description="Ask ComfyUI to release models after generated images finish."
          value={comfyUi.unloadAfterBatch}
          onChange={(value) => updateComfyUi({ unloadAfterBatch: value })}
        />
        <ToggleRow
          title="Open Browser"
          description="Open ComfyUI's browser window when the server starts."
          value={comfyUi.openBrowser}
          onChange={(value) => updateComfyUi({ openBrowser: value })}
        />
      </Section>
    </>
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

function FieldShell({ label, children }) {
  return (
    <label className="block">
      <span className="mb-2 block text-xs font-medium uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      {children}
    </label>
  );
}

function TextField({ label, value, onChange }) {
  return (
    <FieldShell label={label}>
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-11 w-full rounded-lg border border-zinc-800/70 bg-zinc-950 px-3 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-700 focus:border-zinc-500"
      />
    </FieldShell>
  );
}

function NumberField({ label, value, onChange, min, step = 1 }) {
  return (
    <FieldShell label={label}>
      <input
        type="number"
        min={min}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-11 w-full rounded-lg border border-zinc-800/70 bg-zinc-950 px-3 text-sm text-zinc-100 outline-none transition-colors focus:border-zinc-500"
      />
    </FieldShell>
  );
}

function SelectField({ label, value, options, onChange }) {
  return (
    <FieldShell label={label}>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-11 w-full rounded-lg border border-zinc-800/70 bg-zinc-950 px-3 text-sm text-zinc-100 outline-none transition-colors focus:border-zinc-500"
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </FieldShell>
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
