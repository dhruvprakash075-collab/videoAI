import { useEffect, useRef, useState, useCallback } from 'react';
import { apiGet, apiSend } from '../lib/api.js';

const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = new Set(['ready', 'error']);

export default function useABJob() {
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState('idle');
  const [images, setImages] = useState({ a: [], b: [] });
  const [lastPickResult, setLastPickResult] = useState(null);
  const pollRef = useRef(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback((id) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const data = await apiGet(`/api/ab/status/${id}`);
        if (TERMINAL_STATUSES.has(data.status)) {
          stopPolling();
          setStatus(data.status);
          setImages({ a: data.images_a ?? [], b: data.images_b ?? [] });
        }
      } catch { /* network error — keep polling */ }
    }, POLL_INTERVAL_MS);
  }, [stopPolling]);

  const start = useCallback(async (segmentNum, promptA, promptB, topic) => {
    setStatus('starting');
    const formData = new FormData();
    formData.append('segment_num', segmentNum);
    formData.append('prompt_a', promptA);
    formData.append('prompt_b', promptB);
    if (topic) formData.append('topic', topic);
    try {
      const res = await apiSend('/api/ab/generate', formData);
      const data = res.ok ? await res.json() : null;
      if (!data?.job_id) {
        setStatus('error');
        return;
      }
      setJobId(data.job_id);
      setStatus('running');
      startPolling(data.job_id);
    } catch {
      setStatus('error');
    }
  }, [startPolling]);

  const pick = useCallback(async (choice, segmentNum) => {
    if (!jobId) return;
    const formData = new FormData();
    formData.append('job_id', jobId);
    formData.append('choice', choice);
    formData.append('segment_num', segmentNum);
    try {
      const res = await apiSend('/api/ab/pick', formData);
      setLastPickResult(res.ok ? await res.json() : { error: 'pick failed' });
      setStatus('idle');
      setJobId(null);
      setImages({ a: [], b: [] });
    } catch { /* network error — leave state alone */ }
  }, [jobId]);

  useEffect(() => stopPolling, [stopPolling]);

  return { status, images, jobId, lastPickResult, start, pick };
}
