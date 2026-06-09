import { useEffect, useState } from 'react';
import { FileText, Video, ExternalLink } from 'lucide-react';
import JobLogViewer from './JobLogViewer.jsx';
import JobActions from './JobActions.jsx';
import { apiGet } from '../lib/api.js';

function outputUrl(outputPath) {
  if (!outputPath) return null;
  const normalized = outputPath.replace(/\\/g, '/');
  const match = normalized.match(/[/\\]studio_outputs[/\\](.+)/);
  return match ? `/studio_outputs/${match[1]}` : normalized;
}

export default function JobDetail({ jobId, onClose }) {
  const [job, setJob] = useState(null);
  const [artifacts, setArtifacts] = useState(null);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        const data = await apiGet(`/api/jobs/${jobId}`);
        if (mounted) setJob(data);
      } catch (e) {
        console.error('Failed to load job', e);
      }
    }
    load();
    const t = setInterval(load, 1500);
    return () => {
      mounted = false;
      clearInterval(t);
    };
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    apiGet(`/api/jobs/${jobId}/artifacts`)
      .then((data) => {
        const items = data?.artifacts ?? [];
        const byKey = {};
        for (const a of items) {
          byKey[a.key] = a;
        }
        setArtifacts({
          video: byKey.output_video?.path
            ? outputUrl(byKey.output_video.path)
            : null,
          manifest: byKey.manifest?.path != null,
          items,
        });
      })
      .catch(() => {});
  }, [jobId]);

  if (!job) return null;

  const outputUrl_ = outputUrl(job.output_path);

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-6 z-50">
      <div className="bg-[#0b0b0d] p-6 rounded-lg w-full max-w-4xl max-h-[90vh] overflow-y-auto">
        <div className="flex justify-between items-start">
          <div>
            <h3 className="text-xl">Job #{job.id} — {job.topic}</h3>
            <div className="text-sm text-zinc-400">Status: {job.status} — Attempt: {job.attempt}</div>
          </div>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-3 py-1 rounded bg-zinc-800">Close</button>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4">
          <div>
            <h4 className="text-sm text-zinc-300">Request</h4>
            <pre className="text-xs p-2 bg-zinc-900 rounded mt-2 max-h-48 overflow-auto">{JSON.stringify(JSON.parse(job.request_json || '{}'), null, 2)}</pre>
          </div>

          <div>
            <h4 className="text-sm text-zinc-300">Artifacts</h4>
            <div className="text-xs text-zinc-400 mt-2">Output: {job.output_path || '—'}</div>
            <div className="text-xs text-zinc-400">Error: {job.error || '—'}</div>
            {outputUrl_ && (
              <a
                href={outputUrl_}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 text-xs text-zinc-300 hover:text-white mt-2 bg-zinc-800 p-2 rounded"
              >
                <Video size={12} /> Open Output <ExternalLink size={10} />
              </a>
            )}
            {artifacts?.manifest && (
              <div className="mt-1">
                <span className="flex items-center gap-1 text-[10px] text-zinc-500">
                  <FileText size={10} /> Has manifest
                </span>
              </div>
            )}
          </div>
        </div>

        {outputUrl_ && (artifacts?.thumbnail || artifacts?.video) && (
          <div className="mt-4">
            {artifacts?.thumbnail && (
              <img src={artifacts.thumbnail} alt="" className="max-h-48 rounded-lg object-cover" />
            )}
            {artifacts?.video && (
              <video src={artifacts.video} controls className="w-full max-h-64 rounded-lg mt-2" />
            )}
          </div>
        )}

        <div className="mt-4">
          <JobActions job={job} />
        </div>

        <div className="mt-4">
          <h4 className="text-sm text-zinc-300">Logs</h4>
          <JobLogViewer jobId={jobId} />
        </div>
      </div>
    </div>
  );
}
