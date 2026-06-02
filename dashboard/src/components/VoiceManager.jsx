import { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, Play, Loader2 } from 'lucide-react';

export default function VoiceManager() {
  const [voices, setVoices] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [characterName, setCharacterName] = useState('');
  const [playingVoice, setPlayingVoice] = useState(null);
  const audioRef = useRef(null);
  const fileInputRef = useRef(null);

  const fetchVoices = useCallback(async () => {
    try {
      const res = await fetch('/api/voices');
      if (res.ok) {
        const data = await res.json();
        setVoices(data.voices || []);
      }
    } catch {
      console.error("Failed to fetch voices");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/voices')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (!cancelled && data) setVoices(data.voices || []); })
      .catch(() => console.error("Failed to fetch voices"));
    return () => { cancelled = true; };
  }, []);

  // P2-17: Play a voice preview using the HTML5 Audio API
  const playVoice = (voiceName) => {
    // Stop any currently playing preview
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (playingVoice === voiceName) {
      // Toggle off — already stopped above
      setPlayingVoice(null);
      return;
    }
    const audio = new Audio(`/api/audio/preview/${encodeURIComponent(voiceName)}`);
    audioRef.current = audio;
    setPlayingVoice(voiceName);
    audio.play().catch((err) => {
      console.error('Voice preview failed:', err);
      setPlayingVoice(null);
    });
    audio.onended = () => {
      setPlayingVoice(null);
      audioRef.current = null;
    };
    audio.onerror = () => {
      setPlayingVoice(null);
      audioRef.current = null;
    };
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      // P3-24: validate dropped files — the file input's `accept` attribute only
      // applies to the picker dialog, not drag-and-drop.  Enforce the same
      // .wav/.mp3 type and 10 MB size limit here before calling handleUpload.
      const file = e.dataTransfer.files[0];
      const allowedTypes = ['audio/wav', 'audio/wave', 'audio/x-wav', 'audio/mpeg', 'audio/mp3'];
      const allowedExts = ['.wav', '.mp3'];
      const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
      if (!allowedTypes.includes(file.type) && !allowedExts.includes(ext)) {
        alert('Only WAV or MP3 files are accepted.');
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        alert('File is too large. Maximum size is 10 MB.');
        return;
      }
      handleUpload(file);
    }
  };

  const handleFileSelect = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      handleUpload(e.target.files[0]);
    }
  };

  const handleUpload = async (file) => {
    if (!file) return;
    if (!characterName.trim()) {
      alert("Please enter a character name first.");
      return;
    }

    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('character_name', characterName.trim());

    try {
      const res = await fetch('/api/upload_voice', {
        method: 'POST',
        body: formData,
      });
      if (res.ok) {
        await fetchVoices();
        setCharacterName('');
      } else {
        alert("Upload failed.");
      }
    } catch (err) {
      console.error(err);
      alert("Upload error.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto h-full flex flex-col pt-8 animate-in fade-in duration-500">
      
      <div className="mb-12 text-center">
        <h2 className="text-3xl font-light tracking-tight text-white mb-3">Voice Studio</h2>
        <p className="text-zinc-400 font-light">Manage and upload character reference voices.</p>
      </div>

      <div className="flex gap-8">
        
        {/* Upload Zone */}
        <div className="flex-1">
          <div className="bg-zinc-900/50 border border-zinc-800/50 p-6 rounded-3xl backdrop-blur-sm">
             <h3 className="text-lg font-medium text-white mb-4">Upload New Voice</h3>
             
             <div className="mb-6">
                <label className="block text-sm text-zinc-400 mb-2 ml-1">Character Name</label>
                <input 
                  type="text" 
                  value={characterName}
                  onChange={(e) => setCharacterName(e.target.value)}
                  placeholder="e.g. lumian_lee" 
                  className="w-full bg-zinc-950/50 border border-zinc-800 rounded-xl px-4 py-3 text-white focus:outline-none focus:ring-1 focus:ring-emerald-500/50 transition-all placeholder:text-zinc-600"
                />
             </div>

             <div 
               onDragOver={handleDragOver}
               onDragLeave={handleDragLeave}
               onDrop={handleDrop}
               onClick={() => fileInputRef.current?.click()}
               className={`w-full aspect-video border-2 border-dashed rounded-2xl flex flex-col items-center justify-center cursor-pointer transition-all duration-300
                  ${isDragging ? 'border-emerald-500/50 bg-emerald-500/5' : 'border-zinc-800 hover:border-zinc-700 bg-zinc-950/50 hover:bg-zinc-900/50'}
               `}
             >
                <input type="file" ref={fileInputRef} className="hidden" accept=".wav,.mp3" onChange={handleFileSelect} />
                
                {uploading ? (
                   <Loader2 className="w-8 h-8 text-emerald-500 animate-spin mb-3" />
                ) : (
                   <Mic className={`w-8 h-8 mb-4 transition-colors ${isDragging ? 'text-emerald-500' : 'text-zinc-600'}`} />
                )}
                
                <p className="text-sm font-medium text-zinc-300">
                  {uploading ? 'Processing voice...' : 'Drop raw voice sample here'}
                </p>
                <p className="text-xs text-zinc-500 mt-2">WAV or MP3 up to 10MB</p>
             </div>
          </div>
        </div>

        {/* Voice Gallery */}
        <div className="flex-[1.5]">
           <div className="bg-zinc-900/50 border border-zinc-800/50 p-6 rounded-3xl backdrop-blur-sm h-full">
              <h3 className="text-lg font-medium text-white mb-6">Voice Library</h3>
              
              {voices.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-48 text-zinc-600">
                   <p>No voices uploaded yet.</p>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-4">
                  {voices.map((v) => (
                    <div key={v.name} className="group relative bg-zinc-950/50 border border-zinc-800 rounded-2xl p-4 hover:border-zinc-700 transition-all cursor-pointer overflow-hidden">
                       <div className="flex items-center gap-4">
                          <button
                            onClick={() => playVoice(v.name)}
                            aria-label={`Preview voice ${v.name}`}
                            className="w-10 h-10 rounded-full bg-zinc-900 text-zinc-400 flex items-center justify-center group-hover:bg-emerald-500/10 group-hover:text-emerald-500 transition-colors"
                          >
                             {playingVoice === v.name
                               ? <span className="w-3 h-3 rounded-sm bg-emerald-500 block" />
                               : <Play size={16} className="ml-0.5" />
                             }
                          </button>
                          <div>
                            <p className="text-zinc-200 font-medium truncate w-32">{v.name}</p>
                            <p className="text-xs text-zinc-600 mt-1">{(v.size / 1024).toFixed(1)} KB</p>
                          </div>
                       </div>
                    </div>
                  ))}
                </div>
              )}
           </div>
        </div>

      </div>
    </div>
  );
}
