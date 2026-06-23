import { useState, useRef } from 'react';
import { Send, FileText, Upload } from 'lucide-react';

const RUN_MODES = [
  { value: '', label: 'Default (one_time)' },
  { value: 'one_time', label: 'One Time (isolated)' },
  { value: 'project', label: 'Project (persistent)' },
];

const SOURCE_OPTIONS = [
  { value: 'topic', label: 'Topic Text' },
  { value: 'file', label: 'Upload File' },
  { value: 'text', label: 'Paste Content' },
  { value: 'source', label: 'Source URL/Path' },
];

export default function CreateJobPanel({ onJobQueued }) {
  const [sourceMode, setSourceMode] = useState('topic');
  const [topic, setTopic] = useState('');
  const [pastedText, setPastedText] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [file, setFile] = useState(null);
  const [duration, setDuration] = useState(1);
  const [runMode, setRunMode] = useState('');
  const [directorMode, setDirectorMode] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [preview, setPreview] = useState(false);
  const [evalModels, setEvalModels] = useState(false);
  const [noResume, setNoResume] = useState(true);
  const [yes, setYes] = useState(false);
  const [skipPreflight, setSkipPreflight] = useState(false);
  const [preflightOnly, setPreflightOnly] = useState(false);
  const [wordsPerSegment, setWordsPerSegment] = useState(0);
  const [imagesPerSegment, setImagesPerSegment] = useState(0);
  const [segmentCount, setSegmentCount] = useState(0);
  const [project, setProject] = useState('');
  const [series, setSeries] = useState(false);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState(null);
  const fileRef = useRef(null);

  const buildPayload = () => {
    const payload = {
      topic: topic || 'Untitled Job',
      duration,
      dry_run: dryRun,
      no_resume: noResume,
    };
    if (runMode) payload.run_mode = runMode;
    if (directorMode) payload.director_mode = true;
    if (preview) payload.preview = true;
    if (evalModels) payload.eval_models = true;
    if (yes) payload.yes = true;
    if (skipPreflight) payload.skip_preflight = true;
    if (preflightOnly) payload.preflight_only = true;
    if (wordsPerSegment > 0) payload.words_per_segment = wordsPerSegment;
    if (imagesPerSegment > 0) payload.images_per_segment = imagesPerSegment;
    if (segmentCount > 0) payload.segment_count = segmentCount;
    if (project) payload.project = project;
    if (series) payload.series = true;
    if (sourceMode === 'text' && pastedText.trim()) {
      payload.content_text = pastedText;
    }
    if (sourceMode === 'source' && sourceUrl.trim()) {
      payload.source = sourceUrl.trim();
    }
    return payload;
  };

  const handleSubmit = async () => {
    setSending(true);
    setResult(null);
    let data;
    try {
      if (sourceMode === 'file' && file) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('topic', topic || 'Untitled Job');
        const p = buildPayload();
        Object.entries(p).forEach(([k, v]) => {
          if (k !== 'topic') formData.append(k, String(v));
        });
        const res = await fetch('/api/upload_script', { method: 'POST', body: formData });
        data = await res.json();
      } else {
        const payload = buildPayload();
        const res = await fetch('/api/jobs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        data = await res.json();
      }
      setResult(data);
      if (data?.status === 'queued') {
        setTimeout(() => onJobQueued(), 500);
      }
    } catch (e) {
      data = { status: 'error', message: e.message };
      setResult(data);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto h-full flex flex-col animate-in fade-in duration-500">
      <header className="mb-6">
        <h2 className="text-2xl font-light tracking-tight text-white">Create Job</h2>
        <p className="text-zinc-500 text-sm">Queue a new pipeline job with full options.</p>
      </header>

      <div className="flex-1 overflow-y-auto space-y-6 pr-2">
        {/* Source Mode */}
        <section className="bg-zinc-900/30 border border-zinc-800/50 rounded-2xl p-5">
          <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-400 mb-4">Source</h3>
          <div className="flex gap-2 mb-4">
            {SOURCE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setSourceMode(opt.value)}
                className={`px-3 py-1.5 rounded-lg text-xs transition-colors ${
                  sourceMode === opt.value
                    ? 'bg-zinc-700 text-white'
                    : 'bg-zinc-800/40 text-zinc-500 hover:text-zinc-300'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <div className="space-y-3">
            <div>
              <label className="block text-xs text-zinc-500 mb-1">Topic</label>
              <input
                type="text"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="e.g., A Journey Through Space"
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-700 focus:outline-none focus:border-zinc-500"
              />
            </div>

            {sourceMode === 'text' && (
              <div>
                <label className="block text-xs text-zinc-500 mb-1">Content Text</label>
                <textarea
                  value={pastedText}
                  onChange={(e) => setPastedText(e.target.value)}
                  rows={4}
                  placeholder="Paste your script or story content here..."
                  className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-700 focus:outline-none focus:border-zinc-500 resize-none"
                />
              </div>
            )}

            {sourceMode === 'source' && (
              <div>
                <label className="block text-xs text-zinc-500 mb-1">Source URL or Path</label>
                <input
                  type="text"
                  value={sourceUrl}
                  onChange={(e) => setSourceUrl(e.target.value)}
                  placeholder="https://example.com/story.txt or ./path/to/document.pdf"
                  className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-700 focus:outline-none focus:border-zinc-500"
                />
                <p className="text-[10px] text-zinc-600 mt-1">Supports .txt, .md, .pdf, .docx URLs or local paths</p>
              </div>
            )}

            {sourceMode === 'file' && (
              <div>
                <label className="block text-xs text-zinc-500 mb-1">Upload File</label>
                <div
                  onClick={() => fileRef.current?.click()}
                  className="border border-dashed border-zinc-700 rounded-xl p-6 text-center cursor-pointer hover:border-zinc-500 transition-colors"
                >
                  {file ? (
                    <div className="flex items-center justify-center gap-2 text-zinc-300">
                      <FileText size={16} />
                      <span className="text-sm">{file.name}</span>
                    </div>
                  ) : (
                    <div className="text-zinc-500">
                      <Upload size={20} className="mx-auto mb-2" />
                      <p className="text-xs">Click to upload .txt or .md (text only)</p>
                    </div>
                  )}
                  <input
                    ref={fileRef}
                    type="file"
                    accept=".txt,.md"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                    className="hidden"
                  />
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Pipeline Options */}
        <section className="bg-zinc-900/30 border border-zinc-800/50 rounded-2xl p-5">
          <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-400 mb-4">Pipeline</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs text-zinc-500 mb-1">Duration (min)</label>
              <input
                type="number"
                min={0.5}
                step={0.5}
                value={duration}
                onChange={(e) => setDuration(parseFloat(e.target.value) || 1)}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
              />
            </div>
            <div>
              <label className="block text-xs text-zinc-500 mb-1">Run Mode</label>
              <select
                value={runMode}
                onChange={(e) => setRunMode(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
              >
                {RUN_MODES.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-zinc-500 mb-1">Project</label>
              <input
                type="text"
                value={project}
                onChange={(e) => setProject(e.target.value)}
                placeholder="Optional"
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white placeholder:text-zinc-700 focus:outline-none focus:border-zinc-500"
              />
            </div>
            <div>
              <label className="block text-xs text-zinc-500 mb-1">Eval Models</label>
              <select
                value={evalModels ? 'yes' : 'no'}
                onChange={(e) => setEvalModels(e.target.value === 'yes')}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
              >
                <option value="no">No</option>
                <option value="yes">Yes (eval harness only)</option>
              </select>
            </div>
          </div>
        </section>

        {/* Advanced Options */}
        <section className="bg-zinc-900/30 border border-zinc-800/50 rounded-2xl p-5">
          <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-400 mb-4">Advanced</h3>
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div>
              <label className="block text-xs text-zinc-500 mb-1">Words/Segment</label>
              <input
                type="number"
                min={0}
                value={wordsPerSegment}
                onChange={(e) => setWordsPerSegment(parseInt(e.target.value) || 0)}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
              />
            </div>
            <div>
              <label className="block text-xs text-zinc-500 mb-1">Images/Segment</label>
              <input
                type="number"
                min={0}
                value={imagesPerSegment}
                onChange={(e) => setImagesPerSegment(parseInt(e.target.value) || 0)}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
              />
            </div>
            <div>
              <label className="block text-xs text-zinc-500 mb-1">Segment Count</label>
              <input
                type="number"
                min={0}
                value={segmentCount}
                onChange={(e) => setSegmentCount(parseInt(e.target.value) || 0)}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-zinc-500"
              />
            </div>
          </div>
          <div className="space-y-2">
            <ToggleRow label="Dry Run" value={dryRun} onChange={setDryRun} />
            <ToggleRow label="Preview Mode" value={preview} onChange={setPreview} />
            <ToggleRow label="Director Mode" value={directorMode} onChange={setDirectorMode} />
            <ToggleRow label="Series Mode" value={series} onChange={setSeries} />
            <ToggleRow label="Eval Models" value={evalModels} onChange={setEvalModels} />
            <ToggleRow label="No Resume" value={noResume} onChange={setNoResume} />
            <ToggleRow label="Auto-Accept (--yes)" value={yes} onChange={setYes} />
            <ToggleRow label="Skip Preflight" value={skipPreflight} onChange={setSkipPreflight} />
            <ToggleRow label="Preflight Only" value={preflightOnly} onChange={setPreflightOnly} />
          </div>
        </section>

        {/* Submit */}
        <div className="pb-6">
          <button
            onClick={handleSubmit}
            disabled={sending}
            className="w-full py-3 rounded-xl bg-white text-black font-medium hover:bg-zinc-200 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            <Send size={16} />
            {sending ? 'Queueing...' : 'Queue Job'}
          </button>

          {result && (
            <div className={`mt-3 p-3 rounded-lg text-xs ${
              result.status === 'queued' ? 'bg-green-950/30 text-green-400' : 'bg-red-950/30 text-red-400'
            }`}>
              {result.status === 'queued'
                ? `Job #${result.job_id} queued successfully.`
                : `Error: ${result.message || result.status}`}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ToggleRow({ label, value, onChange }) {
  return (
    <label className="flex items-center justify-between py-1.5">
      <span className="text-xs text-zinc-400">{label}</span>
      <button
        type="button"
        onClick={() => onChange(!value)}
        className={`w-9 h-5 rounded-full transition-colors relative ${
          value ? 'bg-zinc-500' : 'bg-zinc-800'
        }`}
      >
        <span
          className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
            value ? 'translate-x-4' : 'translate-x-0.5'
          }`}
        />
      </button>
    </label>
  );
}
