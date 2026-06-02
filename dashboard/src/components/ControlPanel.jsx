import { useState, useEffect, useRef } from 'react';
import { X, Settings2, Sliders, Type, Mic2 } from 'lucide-react';

const API_BASE = '';

export default function ControlPanel({ onClose }) {
  const [config, setConfig] = useState({
    voiceEngine: 'omnivoice', // omnivoice, edge
    dynamicSubtitles: true,
    uncappedScaling: false,
    maxImagesPerSegment: 6,
  });
  const [saving, setSaving] = useState(false);
  const abortRef = useRef(null);

  useEffect(() => {
    // P4-31 fix: use AbortController so the fetch is cancelled on unmount
    const controller = new AbortController();
    abortRef.current = controller;
    fetch(`${API_BASE}/api/config`, { signal: controller.signal })
      .then((res) => res.json())
      .then((data) => {
        if (data && !data.status) {
          setConfig(data);
        }
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          console.error("Failed to load configuration:", err);
        }
      });
    return () => controller.abort();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      const formData = new FormData();
      formData.append('voice_engine', config.voiceEngine);
      formData.append('dynamic_subtitles', String(config.dynamicSubtitles));
      formData.append('uncapped_scaling', String(config.uncappedScaling));
      formData.append('max_images_per_segment', config.maxImagesPerSegment);

      const res = await fetch(`${API_BASE}/api/config`, {
        method: 'POST',
        body: formData,
      });
      const data = await res.json();
      if (data.status === 'success') {
        onClose();
      } else {
        alert("Failed to save settings: " + data.message);
      }
    } catch (err) {
      console.error("Save failed:", err);
      alert("Save failed: " + err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#0a0a0c] text-zinc-200">
      <div className="px-8 py-6 border-b border-zinc-800/50 flex items-center justify-between">
        <h2 className="text-sm font-medium tracking-widest uppercase flex items-center gap-3">
          <Settings2 size={16} className="text-zinc-500" /> System Config
        </h2>
        <button onClick={onClose} className="text-zinc-600 hover:text-white transition-colors">
          <X size={20} strokeWidth={1.5} />
        </button>
      </div>

      <div className="p-8 flex-1 overflow-y-auto flex flex-col gap-10">
        
        {/* Voice Engine Setting */}
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-zinc-400 mb-2">
            <Mic2 size={16} />
            <h3 className="text-xs font-medium uppercase tracking-wider">Voice Engine</h3>
          </div>
          <div className="grid grid-cols-1 gap-2">
            {['omnivoice', 'edge'].map((engine) => (
              <button
                key={engine}
                onClick={() => setConfig({...config, voiceEngine: engine})}
                className={`p-4 text-left border rounded-2xl transition-all duration-300 ${
                  config.voiceEngine === engine 
                  ? 'border-zinc-500 bg-zinc-900 text-white' 
                  : 'border-zinc-800/50 bg-zinc-950/30 text-zinc-500 hover:border-zinc-700 hover:text-zinc-300'
                }`}
              >
                <div className="font-medium capitalize">{engine === 'omnivoice' ? 'OmniVoice (Default)' : 'Edge TTS'}</div>
                <div className="text-xs mt-1 opacity-60">
                  {engine === 'omnivoice' ? 'Ultra-expressive local cloning.' : 'Fast, reliable cloud voices.'}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Visual Scaling Setting */}
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-zinc-400 mb-2">
            <Sliders size={16} />
            <h3 className="text-xs font-medium uppercase tracking-wider">Visual Generation</h3>
          </div>
          
          <div className="flex items-center justify-between p-4 border border-zinc-800/50 rounded-2xl bg-zinc-950/30">
             <div>
               <div className="font-medium text-sm">Uncapped Scaling</div>
               <div className="text-xs text-zinc-500 mt-1 max-w-[200px]">Allow Director AI to dictate exact number of images per segment.</div>
             </div>
             <button 
               onClick={() => setConfig({...config, uncappedScaling: !config.uncappedScaling})}
               aria-label={`Uncapped Scaling: ${config.uncappedScaling ? 'on' : 'off'}`}
               className={`w-12 h-6 rounded-full transition-colors relative ${config.uncappedScaling ? 'bg-zinc-200' : 'bg-zinc-800'}`}
             >
               <div className={`w-4 h-4 rounded-full bg-[#0a0a0c] absolute top-1 transition-all ${config.uncappedScaling ? 'left-7' : 'left-1'}`}></div>
             </button>
          </div>

          {!config.uncappedScaling && (
             <div className="p-4 border border-zinc-800/50 rounded-2xl bg-zinc-950/30">
               <div className="flex justify-between text-sm mb-4">
                 <span className="font-medium">Images Per Segment</span>
                 <span className="text-zinc-400">{config.maxImagesPerSegment}</span>
               </div>
               <input 
                 type="range" min="1" max="15" 
                 value={config.maxImagesPerSegment}
                 aria-label="Images per segment"
                 onChange={(e) => setConfig({...config, maxImagesPerSegment: parseInt(e.target.value)})}
                 className="w-full accent-zinc-500"
               />
             </div>
          )}
        </div>

        {/* Subtitles Setting */}
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-zinc-400 mb-2">
            <Type size={16} />
            <h3 className="text-xs font-medium uppercase tracking-wider">Post-Production</h3>
          </div>
          
          <div className="flex items-center justify-between p-4 border border-zinc-800/50 rounded-2xl bg-zinc-950/30">
             <div>
               <div className="font-medium text-sm">TikTok-Style Subtitles</div>
               <div className="text-xs text-zinc-500 mt-1 max-w-[200px]">Dynamically burn animated subtitles onto final video.</div>
             </div>
             <button 
               onClick={() => setConfig({...config, dynamicSubtitles: !config.dynamicSubtitles})}
               aria-label={`TikTok-Style Subtitles: ${config.dynamicSubtitles ? 'on' : 'off'}`}
               className={`w-12 h-6 rounded-full transition-colors relative ${config.dynamicSubtitles ? 'bg-zinc-200' : 'bg-zinc-800'}`}
             >
               <div className={`w-4 h-4 rounded-full bg-[#0a0a0c] absolute top-1 transition-all ${config.dynamicSubtitles ? 'left-7' : 'left-1'}`}></div>
             </button>
          </div>
        </div>

      </div>

      <div className="p-8 border-t border-zinc-800/50">
        <button 
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
