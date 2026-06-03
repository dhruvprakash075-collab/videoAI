import { useEffect, useState, useCallback } from 'react';
import { apiGet } from '../lib/api.js';

export default function useVoices() {
  const [voices, setVoices] = useState([]);

  const refresh = useCallback(async () => {
    try {
      const data = await apiGet('/api/voices');
      setVoices(data.voices || []);
    } catch {
      console.error('Failed to fetch voices');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiGet('/api/voices')
      .then((data) => { if (!cancelled) setVoices(data.voices || []); })
      .catch(() => console.error('Failed to fetch voices'));
    return () => { cancelled = true; };
  }, []);

  return { voices, refresh };
}
