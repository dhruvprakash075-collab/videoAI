import { useState } from 'react';
import { apiSend } from '../lib/api.js';

export default function ConsultationModal({ question, onClose }) {
  const [reply, setReply] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const trimmed = reply.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    const formData = new FormData();
    formData.append('reply', trimmed);
    try {
      const res = await apiSend('/api/consultation_reply', formData);
      if (res.ok) {
        setReply('');
        onClose();
      }
    } catch (err) {
      console.error(err);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="absolute inset-0 bg-black/60 backdrop-blur-md z-50 flex items-center justify-center p-8 animate-in fade-in zoom-in-95 duration-300">
      <div className="bg-[#0f0f13] border border-zinc-800 shadow-2xl rounded-3xl max-w-2xl w-full p-8 relative overflow-hidden">
        <div className="absolute top-0 left-0 w-full h-1 bg-amber-500" />
        <h2 className="text-xl font-medium text-white mb-2">Director Paused</h2>
        <p className="text-zinc-400 font-light mb-6 leading-relaxed">{question}</p>
        <textarea
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          placeholder="Type your creative direction or feedback here..."
          className="w-full bg-zinc-900 border border-zinc-800 rounded-xl p-4 text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-amber-500/50 transition-all min-h-[120px] mb-4 font-light"
        />
        <div className="flex justify-end">
          <button
            type="button"
            onClick={submit}
            disabled={submitting}
            className="px-6 py-2.5 bg-white text-black font-medium rounded-full hover:bg-zinc-200 transition-colors disabled:opacity-50"
          >
            {submitting ? 'Sending...' : 'Send & Resume Engine'}
          </button>
        </div>
      </div>
    </div>
  );
}
