import { useEffect, useState } from 'react';
import JobDetail from './JobDetail.jsx';
import { apiGet } from '../lib/api.js';

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

  return (
    <div className="text-zinc-100">
      <h2 className="text-xl mb-4">Jobs</h2>
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
