import { useEffect, useState } from 'react';
import JobLogViewer from './JobLogViewer.jsx';
import JobActions from './JobActions.jsx';
import { apiGet } from '../lib/api.js';

export default function JobDetail({ jobId, onClose }) {
  const [job, setJob] = useState(null);

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

  if (!job) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-6 z-50">
      <div className="bg-[#0b0b0d] p-6 rounded-lg w-full max-w-3xl">
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
          </div>
        </div>

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
