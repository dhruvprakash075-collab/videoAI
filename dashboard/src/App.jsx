import { useState, useEffect, useRef } from 'react';
import { Settings, Play, Image as ImageIcon, Menu, X, Mic, Upload, Activity, AlignLeft } from 'lucide-react';
import ControlPanel from './components/ControlPanel';
import StatusTracker from './components/StatusTracker';
import ABPlayground from './components/ABPlayground';
import VoiceManager from './components/VoiceManager';

// P3-20: single source of truth for the API base URL
const API_BASE = '';

export default function App() {
  const [activeTab, setActiveTab] = useState('preview'); // 'preview', 'voices', 'ab-testing'
  const [showSettings, setShowSettings] = useState(false);
  const [status, setStatus] = useState({ state: 'idle', logs: [], video: null, active_question: null });
  const [userReply, setUserReply] = useState('');
  // P3-24: ref to reset the file input after upload so same-file re-upload fires onChange
  const scriptInputRef = useRef(null);

  // Poll backend status
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/status`);
        if (res.ok) {
          const data = await res.json();
          setStatus({ 
            state: data.status, 
            logs: data.logs, 
            video: data.output_video,
            active_question: data.active_question 
          });
        }
      } catch (e) {
        // silently fail on dev polling
      }
    }, 1500);
    return () => clearInterval(interval);
  }, []);

  const handleScriptUpload = async (e) => {
    if (!e.target.files || e.target.files.length === 0) return;
    const file = e.target.files[0];
    const formData = new FormData();
    formData.append('file', file);
    formData.append('topic', file.name.replace('.txt', ''));
    
    try {
      const res = await fetch(`${API_BASE}/api/upload_script`, {
        method: 'POST',
        body: formData
      });
      // P3-24: check res.ok and surface errors (e.g. 409 pipeline already running)
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(`Upload failed: ${data.message || res.statusText}`);
      }
    } catch (e) {
      console.error(e);
      alert(`Upload error: ${e.message}`);
    } finally {
      // P3-24: reset the file input so the same file can be re-uploaded
      if (scriptInputRef.current) scriptInputRef.current.value = '';
    }
  };

  const handleManualPause = async () => {
    try {
      await fetch(`${API_BASE}/api/manual_pause`, { method: 'POST' });
    } catch (e) {
      console.error(e);
    }
  };

  const handleConsultationReply = async () => {
    if (!userReply.trim()) return;
    const formData = new FormData();
    formData.append('reply', userReply);
    try {
      const res = await fetch(`${API_BASE}/api/consultation_reply`, {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        setUserReply('');
        // P3-24: close the consultation modal immediately on submit instead of
        // waiting for the next poll to clear active_question
        setStatus((prev) => ({ ...prev, state: 'running', active_question: null }));
      }
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="flex h-screen w-full bg-[#0a0a0c] text-zinc-100 overflow-hidden font-sans selection:bg-zinc-800">
      
      {/* Sleek Minimalist Sidebar */}
      <div className="w-16 border-r border-zinc-800/40 bg-[#0a0a0c] flex flex-col items-center py-6 gap-8 z-20">
        <div className="w-8 h-8 flex items-center justify-center font-serif italic text-2xl text-white mb-2">
          V
        </div>
        
        <div className="flex flex-col gap-4">
          <button onClick={() => setActiveTab('preview')} title="Director Canvas" className={`p-2.5 rounded-xl transition-all duration-300 ${activeTab === 'preview' ? 'bg-zinc-800/80 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}>
            <Play size={18} strokeWidth={2} />
          </button>
          <button onClick={() => setActiveTab('voices')} title="Voice Studio" className={`p-2.5 rounded-xl transition-all duration-300 ${activeTab === 'voices' ? 'bg-zinc-800/80 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}>
            <Mic size={18} strokeWidth={2} />
          </button>
          <button onClick={() => setActiveTab('ab-testing')} title="A/B Testing" className={`p-2.5 rounded-xl transition-all duration-300 ${activeTab === 'ab-testing' ? 'bg-zinc-800/80 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}>
            <ImageIcon size={18} strokeWidth={2} />
          </button>
        </div>
        
        <div className="mt-auto">
          <button onClick={() => setShowSettings(!showSettings)} title="Settings" className={`p-2.5 rounded-xl transition-all duration-300 ${showSettings ? 'bg-zinc-800/80 text-white shadow-sm' : 'text-zinc-500 hover:text-zinc-300'}`}>
            <Settings size={18} strokeWidth={2} />
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col relative transition-all duration-500 bg-[#0a0a0c]">
        {/* Top Header */}
        <header className="h-16 flex items-center px-8 justify-between z-10 border-b border-zinc-800/20">
          <h1 className="text-sm font-medium text-zinc-400 uppercase tracking-widest">
            {activeTab === 'preview' ? 'Director Canvas' : activeTab === 'voices' ? 'Voice Studio' : 'A/B Testing'}
          </h1>
          <div className="flex items-center gap-4">
            {status.state === 'running' && (
              <button onClick={handleManualPause} className="text-xs font-medium px-4 py-1.5 bg-zinc-900 border border-zinc-800 rounded-full text-zinc-300 hover:bg-zinc-800 transition-colors">
                Pause Engine
              </button>
            )}
            <span className="flex items-center gap-2 text-[10px] uppercase font-mono px-3 py-1.5 bg-zinc-900/50 border border-zinc-800/50 rounded-full text-zinc-400">
              <span className={`w-1.5 h-1.5 block rounded-full ${status.state === 'running' ? 'bg-emerald-500 animate-pulse' : status.state === 'paused' ? 'bg-amber-500' : status.state === 'error' ? 'bg-red-500' : 'bg-zinc-600'}`}></span>
              {status.state}
            </span>
          </div>
        </header>

        {/* Workspace Canvas */}
        <main className="flex-1 overflow-auto p-8 relative">
          {activeTab === 'preview' && (
            <div className="max-w-4xl mx-auto h-full flex flex-col items-center justify-center animate-in fade-in duration-700">
               {status.video ? (
                  <video src={`${API_BASE}${status.video}`} controls className="w-full max-h-[65vh] rounded-xl shadow-2xl bg-black ring-1 ring-zinc-800" />
               ) : (
                  <div className="w-full aspect-video rounded-3xl border border-zinc-800/50 bg-zinc-900/20 flex flex-col items-center justify-center text-zinc-500 relative overflow-hidden">
                    <AlignLeft size={32} className="mb-4 opacity-30" strokeWidth={1.5} />
                    <p className="font-light tracking-wide text-zinc-400">Upload Lore Script</p>
                    <p className="text-xs mt-2 opacity-50 font-mono">.txt format only</p>
                    <input ref={scriptInputRef} type="file" accept=".txt" onChange={handleScriptUpload} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" />
                  </div>
               )}
            </div>
          )}

          {activeTab === 'voices' && <VoiceManager />}
          {activeTab === 'ab-testing' && <ABPlayground />}
        </main>
        
        {/* Floating Minimal Log Viewer (Bottom Right) */}
        <div className="absolute bottom-6 right-8 w-96">
           <StatusTracker logs={status.logs} currentState={status.state} />
        </div>
      </div>

      {/* Human-in-the-Loop Creative Pause Modal */}
      {status.state === 'paused' && status.active_question && (
        <div className="absolute inset-0 bg-black/60 backdrop-blur-md z-50 flex items-center justify-center p-8 animate-in fade-in zoom-in-95 duration-300">
          <div className="bg-[#0f0f13] border border-zinc-800 shadow-2xl rounded-3xl max-w-2xl w-full p-8 relative overflow-hidden">
             <div className="absolute top-0 left-0 w-full h-1 bg-amber-500"></div>
             <h2 className="text-xl font-medium text-white mb-2">Director Paused</h2>
             <p className="text-zinc-400 font-light mb-6 leading-relaxed">
               {status.active_question}
             </p>
             <textarea 
               value={userReply}
               onChange={(e) => setUserReply(e.target.value)}
               placeholder="Type your creative direction or feedback here..."
               className="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-4 text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500/50 transition-all min-h-[120px] mb-4 font-light"
             />
             <div className="flex justify-end">
               <button 
                 onClick={handleConsultationReply}
                 className="px-6 py-2.5 bg-white text-black font-medium rounded-full hover:bg-zinc-200 transition-colors"
               >
                 Send & Resume Engine
               </button>
             </div>
          </div>
        </div>
      )}

      {/* Sliding Settings / Control Panel */}
      <div className={`absolute top-0 right-0 h-full w-96 bg-[#0a0a0c] border-l border-zinc-800/50 shadow-2xl transition-transform duration-500 ease-[cubic-bezier(0.2,0.8,0.2,1)] z-40 ${showSettings ? 'translate-x-0' : 'translate-x-full'}`}>
        <ControlPanel onClose={() => setShowSettings(false)} />
      </div>
      
      {/* Overlay when settings is open */}
      {showSettings && (
        <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px] z-30 transition-opacity duration-500 cursor-pointer" onClick={() => setShowSettings(false)}></div>
      )}
    </div>
  );
}
