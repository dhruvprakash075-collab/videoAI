import { useState, useRef, useEffect } from 'react';
import { Send, Trash2, Bot, User, Loader, Copy } from 'lucide-react';
import { apiGet } from '../lib/api.js';

const LS_SESSION_KEY = 'video_ai_chat_session';

export default function AssistantPanel({ status }) {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(LS_SESSION_KEY) || '');
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    if (sessionId) {
      apiGet(`/api/chat/sessions/${sessionId}`)
        .then((data) => setMessages(data.messages || []))
        .catch(() => {});
    }
  }, [sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const sendMessage = async () => {
    const msg = input.trim();
    if (!msg || loading) return;
    setInput('');
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, session_id: sessionId }),
      });
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        setSessionId(data.session_id);
        localStorage.setItem(LS_SESSION_KEY, data.session_id);
        setMessages(data.messages || []);
      }
    } catch {
      setError('Failed to reach backend. Is it running?');
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const clearSession = () => {
    if (sessionId) {
      apiGet(`/api/chat/sessions/${sessionId}`).catch(() => {}).then(() => {
        fetch(`/api/chat/sessions/${sessionId}`, { method: 'DELETE' }).catch(() => {});
      });
    }
    setSessionId('');
    setMessages([]);
    localStorage.removeItem(LS_SESSION_KEY);
  };

  const copyConversation = async () => {
    const markdown = messages
      .map((msg) => `### ${msg.role === 'user' ? 'User' : 'Assistant'}\n\n${msg.content}`)
      .join('\n\n');
    await navigator.clipboard.writeText(markdown);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="max-w-4xl mx-auto h-full flex flex-col animate-in fade-in duration-500">
      <header className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-light tracking-tight text-white">Assistant</h2>
          <p className="text-zinc-500 text-sm">Ask about system status, jobs, or next steps.</p>
        </div>
        {messages.length > 0 && (
          <div className="flex items-center gap-3">
            <button
              onClick={copyConversation}
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-white transition-colors"
              title="Copy full conversation as Markdown"
            >
              <Copy size={14} /> {copied ? 'Copied' : 'Share'}
            </button>
            <button
              onClick={clearSession}
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-red-400 transition-colors"
            >
              <Trash2 size={14} /> Clear
            </button>
          </div>
        )}
      </header>

      {/* Context chips */}
      <div className="flex gap-2 mb-4 flex-wrap">
        <span className="text-[10px] px-2 py-1 rounded-full bg-zinc-800/60 text-zinc-400">
          Status: {status?.state || 'idle'}
        </span>
        {status?.latestJob && (
          <span className="text-[10px] px-2 py-1 rounded-full bg-zinc-800/60 text-zinc-400">
            Latest: {status.latestJob}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto space-y-4 pr-2 mb-4">
        {messages.length === 0 && !loading && (
          <div className="text-center text-zinc-600 mt-12">
            <Bot size={32} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">Start a conversation with the AI assistant.</p>
            <p className="text-xs mt-1">Ask about jobs, configuration, or next steps.</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
            {msg.role !== 'user' && (
              <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center shrink-0">
                <Bot size={14} className="text-zinc-400" />
              </div>
            )}
            <div
              className={`max-w-[75%] p-3 rounded-2xl text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'bg-white text-black'
                  : 'bg-zinc-800/50 text-zinc-200'
              }`}
            >
              {msg.content}
            </div>
            {msg.role === 'user' && (
              <div className="w-8 h-8 rounded-full bg-zinc-700 flex items-center justify-center shrink-0">
                <User size={14} className="text-zinc-300" />
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center shrink-0">
              <Bot size={14} className="text-zinc-400" />
            </div>
            <div className="bg-zinc-800/50 p-3 rounded-2xl">
              <Loader size={14} className="animate-spin text-zinc-400" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && (
        <div className="mb-2 text-xs text-red-400 bg-red-950/30 p-2 rounded-lg">{error}</div>
      )}

      <div className="flex gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about the system, jobs, or configuration..."
          rows={1}
          className="flex-1 bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-zinc-500 resize-none"
        />
        <button
          onClick={sendMessage}
          disabled={!input.trim() || loading}
          className="px-4 py-3 bg-white text-black rounded-xl hover:bg-zinc-200 transition-colors disabled:opacity-50"
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  );
}
