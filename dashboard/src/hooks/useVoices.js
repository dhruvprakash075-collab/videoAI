import { useEffect, useState, useCallback } from 'react';
import { apiGet } from '../lib/api.js';

export default function useVoices() {
  const [voices, setVoices] = useState([]);

  useEffect(() => {
    let cancelled = false;
    apiGet('/api/voices')
      .then((data) => { if (!cancelled) setVoices(data.voices || []); })
      .catch(() => console.error('Failed to fetch voices'));
    return () => { cancelled = true; };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const data = await apiGet('/api/voices');
      setVoices(data.voices || []);
    } catch {
      console.error('Failed to fetch voices');
    }
  }, []);

  return { voices, refresh };
}
