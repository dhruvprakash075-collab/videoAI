import { useEffect, useState, useRef } from 'react';
import { apiGet } from '../lib/api.js';

export default function JobLogViewer({ jobId }) {
  const [logs, setLogs] = useState([]);
  const ref = useRef(null);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        const data = await apiGet(`/api/jobs/${jobId}/events`);
        if (mounted) setLogs(data.events || []);
      } catch (e) {
        console.error('Failed to load events', e);
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
    if (ref.current) {
      ref.current.scrollTop = ref.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div ref={ref} className="mt-2 p-2 bg-zinc-900 rounded max-h-64 overflow-auto text-xs">
      {logs.map((l) => (
        <div key={l.id} className="whitespace-pre-wrap">{l.ts} — {l.message}</div>
      ))}
    </div>
  );
}
