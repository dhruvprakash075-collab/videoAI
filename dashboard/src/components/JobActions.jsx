import { apiSend } from '../lib/api.js';

export default function JobActions({ job }) {
  const cancel = async () => {
    try {
      await apiSend(`/api/jobs/${job.id}/cancel`, new FormData());
    } catch (e) {
      console.error('Cancel failed', e);
    }
  };

  const retry = async () => {
    try {
      const res = await apiSend(`/api/jobs/${job.id}/retry`, null, 'POST');
      // no-op; UI will refresh via polling
      return res;
    } catch (e) {
      console.error('Retry failed', e);
    }
  };

  return (
    <div className="flex gap-2">
      {job.status === 'queued' && <button onClick={cancel} className="px-3 py-1 rounded bg-zinc-700">Cancel</button>}
      {job.status === 'running' && <button onClick={cancel} className="px-3 py-1 rounded bg-red-600">Cancel</button>}
      {(job.status === 'failed' || job.status === 'canceled') && (
        <button onClick={retry} className="px-3 py-1 rounded bg-green-600">Retry</button>
      )}
      {job.output_path && <a href={job.output_path} className="px-3 py-1 rounded bg-zinc-800" target="_blank" rel="noreferrer">Open</a>}
    </div>
  );
}
