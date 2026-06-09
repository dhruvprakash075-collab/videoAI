import { useEffect, useState } from 'react';
import { apiGet } from '../lib/api.js';

const POLL_INTERVAL_MS = 1500;

export default function useStatusPolling() {
  const [status, setStatus] = useState({
    state: 'idle',
    logs: [],
    video: null,
    active_question: null,
  });

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const [statusData, jobsData] = await Promise.all([
          apiGet('/api/status'),
          apiGet('/api/jobs?limit=1'),
        ]);
        if (cancelled) return;
        const job = (jobsData.jobs || [])[0];
        setStatus({
          state: statusData.status,
          logs: statusData.logs,
          video: statusData.output_video,
          active_question: statusData.active_question,
          latestJob: job ? `#${job.id} ${job.topic || ''} — ${job.status}` : null,
        });
      } catch {
        // swallow — dashboard keeps last known state on transient failure
      }
    };
    const id = setInterval(tick, POLL_INTERVAL_MS);
    tick();
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return [status, setStatus];
}
