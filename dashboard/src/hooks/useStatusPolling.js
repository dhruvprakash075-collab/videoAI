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
        const data = await apiGet('/api/status');
        if (cancelled) return;
        setStatus({
          state: data.status,
          logs: data.logs,
          video: data.output_video,
          active_question: data.active_question,
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
