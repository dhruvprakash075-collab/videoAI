import { useEffect, useState } from 'react';
import JobDetail from './JobDetail.jsx';
import { apiGet } from '../lib/api.js';
import { AlertTriangle } from 'lucide-react';

export default function JobList() {
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        const data = await apiGet('/api/jobs');
        if (mounted) setJobs(data.jobs || []);
      } catch (e) {
        console.error('Failed to load jobs', e);
      }
    }
    load();
    const t = setInterval(load, 3000);
    return () => {
      mounted = false;
      clearInterval(t);
    };
  }, []);

  const hasQueuedJobs = jobs.some((j) => j.status === 'queued');
  const hasRunningJobs = jobs.some((j) => j.status === 'running');

  return (
    <div className="text-zinc-100">
      <h2 className="text-xl mb-4">Jobs</h2>

      {hasQueuedJobs && !hasRunningJobs && (
        <div className="flex items-center gap-2 p-3 mb-4 rounded-lg bg-amber-950/20 border border-amber-900/30 text-amber-400 text-sm">
          <AlertTriangle size={14} />
          Jobs are queued but no worker appears to be running. Start the worker via launch_studio.bat.
        </div>
      )}

      <div className="grid gap-2">
        {jobs.length === 0 && <div className="text-zinc-400">No jobs yet</div>}
        {jobs.map((j) => (
          <button
            key={j.id}
            onClick={() => setSelected(j.id)}
            className="text-left p-3 bg-zinc-900/50 rounded-md hover:bg-zinc-900/80"
          >
            <div className="flex justify-between">
              <div className="font-medium">#{j.id} {j.topic || ''}</div>
              <div className="text-sm text-zinc-400">{j.status}</div>
            </div>
            <div className="text-xs text-zinc-500">{j.created_at}</div>
          </button>
        ))}
      </div>

      {selected && <JobDetail jobId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
