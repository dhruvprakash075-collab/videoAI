import { useRef, useState } from 'react';
import { Mic, Loader2 } from 'lucide-react';
import { apiSend } from '../lib/api.js';
import { validateVoiceFile } from '../lib/voiceFile.js';

export default function UploadZone({ characterName, onCharacterNameChange, onUploaded }) {
  const fileInputRef = useRef(null);
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);

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
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    const err = validateVoiceFile(file);
    if (err) {
      alert(err);
      return;
    }
    handleUpload(file);
  };
  const handleFileSelect = (e) => {
    const file = e.target.files?.[0];
    if (file) handleUpload(file);
  };

  const handleUpload = async (file) => {
    if (!characterName.trim()) {
      alert('Please enter a character name first.');
      return;
    }
    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('character_name', characterName.trim());
    try {
      const res = await apiSend('/api/upload_voice', formData);
      if (res.ok) {
        await onUploaded();
        onCharacterNameChange('');
      } else {
        alert('Upload failed.');
      }
    } catch (err) {
      console.error(err);
      alert('Upload error.');
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="bg-zinc-900/50 border border-zinc-800/50 p-6 rounded-3xl backdrop-blur-sm">
      <h3 className="text-lg font-medium text-white mb-4">Upload New Voice</h3>

      <div className="mb-6">
        <label className="block text-sm text-zinc-400 mb-2 ml-1">Character Name</label>
        <input
          type="text"
          value={characterName}
          onChange={(e) => onCharacterNameChange(e.target.value)}
          placeholder="e.g. lumian_lee"
          className="w-full bg-zinc-950/50 border border-zinc-800 rounded-xl px-4 py-3 text-white focus:outline-none focus:ring-1 focus:ring-emerald-500/50 transition-all placeholder:text-zinc-600"
        />
      </div>

      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`w-full aspect-video border-2 border-dashed rounded-2xl flex flex-col items-center justify-center cursor-pointer transition-all duration-300 ${
          isDragging
            ? 'border-emerald-500/50 bg-emerald-500/5'
            : 'border-zinc-800 hover:border-zinc-700 bg-zinc-950/50 hover:bg-zinc-900/50'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept=".wav,.mp3"
          onChange={handleFileSelect}
        />
        {uploading ? (
          <Loader2 className="w-8 h-8 text-emerald-500 animate-spin mb-3" />
        ) : (
          <Mic
            className={`w-8 h-8 mb-4 transition-colors ${
              isDragging ? 'text-emerald-500' : 'text-zinc-600'
            }`}
          />
        )}
        <p className="text-sm font-medium text-zinc-300">
          {uploading ? 'Processing voice...' : 'Drop raw voice sample here'}
        </p>
        <p className="text-xs text-zinc-500 mt-2">WAV or MP3 up to 10MB</p>
      </div>
    </div>
  );
}
