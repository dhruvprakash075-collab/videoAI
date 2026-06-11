import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api.js';
import { FileText, Video, Loader, ExternalLink } from 'lucide-react';

export default function ArtifactsPanel() {
  const [artifacts, setArtifacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);

  useEffect(() => {
    apiGet('/api/artifacts')
      .then((data) => setArtifacts(data.artifacts || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const loadDetail = async (runId) => {
    setSelected(runId);
    setDetail(null);
    try {
      const data = await apiGet(`/api/artifacts/${runId}`);
      setDetail(data);
    } catch (e) {
      setDetail({ error: e.message });
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <Loader size={20} className="animate-spin text-zinc-500" />
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto h-full flex flex-col animate-in fade-in duration-500">
      <header className="mb-6">
        <h2 className="text-2xl font-light tracking-tight text-white">Artifacts</h2>
        <p className="text-zinc-500 text-sm">Recent pipeline outputs and run details.</p>
      </header>

      <div className="flex-1 flex gap-6 overflow-hidden">
        <div className="w-72 shrink-0 overflow-y-auto space-y-2">
          {artifacts.length === 0 && (
            <div className="text-zinc-600 text-sm text-center mt-8">No artifacts yet.</div>
          )}
          {artifacts.map((a) => (
            <button
              key={a.run_id}
              onClick={() => loadDetail(a.run_id)}
              className={`w-full text-left p-3 rounded-xl transition-colors ${
                selected === a.run_id
                  ? 'bg-zinc-800/60 border border-zinc-700'
                  : 'bg-zinc-900/30 border border-transparent hover:bg-zinc-800/30'
              }`}
            >
              <div className="text-sm font-medium truncate">{a.run_id}</div>
              <div className="flex gap-2 mt-1">
                {a.video && <Video size={12} className="text-zinc-500" />}
                {a.has_manifest && <FileText size={12} className="text-zinc-500" />}
                {a.has_chapters && <FileText size={12} className="text-zinc-500" />}
              </div>
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto">
          {!detail && (
            <div className="text-zinc-600 text-sm text-center mt-16">Select a run to view details.</div>
          )}
          {detail?.error && (
            <div className="text-red-400 text-sm">{detail.error}</div>
          )}
          {detail && !detail.error && (
            <div className="space-y-4">
              {detail.thumbnail && (
                <img
                  src={detail.thumbnail}
                  alt="Thumbnail"
                  className="w-full max-h-64 object-cover rounded-xl"
                />
              )}
              {detail.video && (
                <a
                  href={detail.video}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-2 text-sm text-zinc-300 hover:text-white bg-zinc-800/40 p-3 rounded-xl"
                >
                  <Video size={16} /> Open Video <ExternalLink size={12} />
                </a>
              )}
              {detail.manifest && (
                <div>
                  <h4 className="text-xs font-medium text-zinc-400 mb-2">Manifest</h4>
                  <pre className="text-[10px] bg-zinc-900 p-3 rounded-lg max-h-48 overflow-auto text-zinc-400">
                    {JSON.stringify(detail.manifest, null, 2)}
                  </pre>
                </div>
              )}
              {detail.chapters && (
                <div>
                  <h4 className="text-xs font-medium text-zinc-400 mb-2">Chapters</h4>
                  <pre className="text-[10px] bg-zinc-900 p-3 rounded-lg max-h-32 overflow-auto text-zinc-400">
                    {detail.chapters}
                  </pre>
                </div>
              )}
              {detail.segments && detail.segments.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-zinc-400 mb-2">Segments</h4>
                  <div className="grid grid-cols-4 gap-2">
                    {detail.segments.map((seg) => (
                      <div key={seg.name} className="bg-zinc-900/50 p-2 rounded-lg">
                        <div className="text-[10px] text-zinc-500 mb-1">{seg.name}</div>
                        {seg.images.length > 0 && (
                          <div className="grid grid-cols-2 gap-1">
                            {seg.images.slice(0, 4).map((img, i) => (
                              <img key={i} src={img} alt="" className="w-full rounded" />
                            ))}
                            {seg.images.length > 4 && (
                              <div className="text-[10px] text-zinc-600">+{seg.images.length - 4}</div>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
