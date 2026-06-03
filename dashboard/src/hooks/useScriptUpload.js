import { useRef } from 'react';
import { apiSend } from '../lib/api.js';

export default function useScriptUpload() {
  const inputRef = useRef(null);

  const upload = async (file) => {
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    formData.append('topic', file.name.replace(/\.txt$/i, ''));

    try {
      const res = await apiSend('/api/upload_script', formData);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(`Upload failed: ${data.message || res.statusText}`);
      }
    } catch (err) {
      console.error(err);
      alert(`Upload error: ${err.message}`);
    } finally {
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  return { inputRef, upload };
}
