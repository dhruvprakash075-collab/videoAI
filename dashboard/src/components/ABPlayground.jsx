import { useState, useRef, useEffect } from 'react';
import { RefreshCw, Check, Zap } from 'lucide-react';

export default function ABPlayground() {
  const [jobId, setJobId] = useState(null);
  const [promptA, setPromptA] = useState("A futuristic city at night, neon lights, cinematic");
  const [promptB, setPromptB] = useState("A futuristic city at night, raining, highly detailed, photorealistic");
  const [status, setStatus] = useState("idle");
  const [images, setImages] = useState({ a: [], b: [] });
  const pollIntervalRef = useRef(null);

  // Clear the poll interval when the component unmounts
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current !== null) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, []);

  const startTest = async () => {
    setStatus("starting");
    const formData = new FormData();
    formData.append('segment_num', 1);
    formData.append('prompt_a', promptA);
    formData.append('prompt_b', promptB);

    try {
      const res = await fetch('/api/ab/generate', { method: 'POST', body: formData });
      const data = res.ok ? await res.json() : null;
      if (!data || !data.job_id) {
        setStatus("error");
        return;
      }
      setJobId(data.job_id);
      setStatus("running");
      pollJob(data.job_id);
    } catch {
      setStatus("error");
    }
  };

  const pollJob = (id) => {
    // Clear any existing interval before starting a new one
    if (pollIntervalRef.current !== null) {
      clearInterval(pollIntervalRef.current);
    }
    pollIntervalRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/ab/status/${id}`);
        const data = await res.json();
        if (data.status === 'ready' || data.status === 'error') {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
          setStatus(data.status);
          setImages({ a: data.images_a ?? [], b: data.images_b ?? [] });
        }
      } catch { /* network error — keep polling */ }
    }, 2000);
  };

  const pickVariant = async (choice) => {
    const formData = new FormData();
    formData.append('job_id', jobId);
    formData.append('choice', choice);
    formData.append('segment_num', 1);

    try {
      await fetch('/api/ab/pick', { method: 'POST', body: formData });
      setStatus("idle");
      setJobId(null);
      setImages({ a: [], b: [] });
    } catch { /* network error — reset to error state */ }
  };

  return (
    <div className="max-w-6xl mx-auto h-full flex flex-col pt-8 animate-in fade-in duration-500">
      
      <div className="mb-10 text-center">
        <h2 className="text-3xl font-light tracking-tight text-white mb-2">A/B Testing Studio</h2>
        <p className="text-zinc-500 font-light text-sm">Visually compare prompts and face-lock accuracy.</p>
      </div>

      <div className="bg-[#0f0f13] border border-zinc-800/50 rounded-3xl p-8 mb-8 shadow-2xl">
        <div className="grid grid-cols-2 gap-8">
          <div>
            <label className="block text-xs font-mono text-emerald-500 uppercase tracking-wider mb-3">Variant A</label>
            <textarea 
              value={promptA} 
              onChange={(e)=>setPromptA(e.target.value)} 
              rows={2} 
              className="w-full bg-zinc-950/50 border border-zinc-800/50 rounded-2xl p-4 text-sm text-zinc-300 focus:outline-none focus:ring-1 focus:ring-emerald-500/50 transition-all resize-none font-light leading-relaxed" 
            />
          </div>
          <div>
            <label className="block text-xs font-mono text-blue-500 uppercase tracking-wider mb-3">Variant B</label>
            <textarea 
              value={promptB} 
              onChange={(e)=>setPromptB(e.target.value)} 
              rows={2} 
              className="w-full bg-zinc-950/50 border border-zinc-800/50 rounded-2xl p-4 text-sm text-zinc-300 focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all resize-none font-light leading-relaxed" 
            />
          </div>
        </div>
        <div className="mt-8 flex justify-center">
          <button 
            onClick={startTest} 
            disabled={status === 'running' || status === 'starting'} 
            className="bg-white hover:bg-zinc-200 text-black font-medium px-8 py-3 rounded-full flex items-center gap-2 transition-colors disabled:opacity-50"
          >
            {status === 'running' ? (
               <RefreshCw size={16} className="animate-spin" />
            ) : (
               <Zap size={16} />
            )}
            {status === 'running' ? 'Generating Images...' : 'Run A/B Comparison'}
          </button>
        </div>
      </div>

      {(images.a.length > 0 || images.b.length > 0) && (
        <div className="flex-1 grid grid-cols-2 gap-8 pb-8 animate-in slide-in-from-bottom-8 duration-700">
           {/* Variant A */}
           <div className="bg-[#0f0f13]/50 border border-zinc-800/50 rounded-3xl p-6 flex flex-col group relative overflow-hidden">
              <div className="absolute top-0 left-0 w-full h-1 bg-emerald-500/20"></div>
              <div className="flex justify-between items-center mb-6">
                 <span className="font-mono text-emerald-500 text-xs tracking-wider uppercase">Output A</span>
                 <button 
                   onClick={() => pickVariant('a')} 
                   className="text-xs bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500 hover:text-white px-4 py-2 rounded-full flex items-center gap-1.5 transition-all"
                 >
                    <Check size={14} /> Commit A
                 </button>
              </div>
              <div className="grid grid-cols-2 gap-3 flex-1">
                 {images.a.map((src, i) => (
                    <div key={src || `a-${i}`} className="rounded-xl overflow-hidden border border-zinc-800/50">
                       <img src={src} alt={`Variant A image ${i + 1}`} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-700" />
                    </div>
                 ))}
              </div>
           </div>

           {/* Variant B */}
           <div className="bg-[#0f0f13]/50 border border-zinc-800/50 rounded-3xl p-6 flex flex-col group relative overflow-hidden">
              <div className="absolute top-0 left-0 w-full h-1 bg-blue-500/20"></div>
              <div className="flex justify-between items-center mb-6">
                 <span className="font-mono text-blue-500 text-xs tracking-wider uppercase">Output B</span>
                 <button 
                   onClick={() => pickVariant('b')} 
                   className="text-xs bg-blue-500/10 text-blue-400 hover:bg-blue-500 hover:text-white px-4 py-2 rounded-full flex items-center gap-1.5 transition-all"
                 >
                    <Check size={14} /> Commit B
                 </button>
              </div>
              <div className="grid grid-cols-2 gap-3 flex-1">
                 {images.b.map((src, i) => (
                    <div key={src || `b-${i}`} className="rounded-xl overflow-hidden border border-zinc-800/50">
                       <img src={src} alt={`Variant B image ${i + 1}`} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-700" />
                    </div>
                 ))}
              </div>
           </div>
        </div>
      )}
    </div>
  );
}
