import { useState } from 'react';
import { Loader, Play, CheckCircle, AlertTriangle, XCircle } from 'lucide-react';

export default function PreflightPanel() {
  const [checks, setChecks] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const runChecks = async () => {
    setLoading(true);
    setError(null);
    setChecks(null);
    try {
      const res = await fetch('/api/preflight');
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        setChecks(data.checks || []);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const statusIcon = (status) => {
    switch (status) {
      case 'ok': return <CheckCircle size={14} className="text-green-400" />;
      case 'warn': return <AlertTriangle size={14} className="text-yellow-400" />;
      case 'fail': return <XCircle size={14} className="text-red-400" />;
      default: return <AlertTriangle size={14} className="text-zinc-500" />;
    }
  };

  const statusBadge = (status) => {
    const colors = {
      ok: 'bg-green-950/30 text-green-400 border-green-900/30',
      warn: 'bg-yellow-950/30 text-yellow-400 border-yellow-900/30',
      fail: 'bg-red-950/30 text-red-400 border-red-900/30',
    };
    return `px-2 py-0.5 rounded text-[10px] border ${colors[status] || 'bg-zinc-800 text-zinc-500'}`;
  };

  return (
    <div className="max-w-3xl mx-auto h-full flex flex-col animate-in fade-in duration-500">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-light tracking-tight text-white">Preflight</h2>
          <p className="text-zinc-500 text-sm">Run readiness checks before starting a pipeline job.</p>
        </div>
        <button
          onClick={runChecks}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-white text-black rounded-xl text-sm font-medium hover:bg-zinc-200 transition-colors disabled:opacity-50"
        >
          {loading ? <Loader size={14} className="animate-spin" /> : <Play size={14} />}
          {loading ? 'Running...' : 'Run Checks'}
        </button>
      </header>

      {error && (
        <div className="bg-red-950/30 border border-red-900/30 p-4 rounded-xl text-sm text-red-400 mb-4">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-1">
        {!checks && !loading && (
          <div className="text-center text-zinc-600 mt-16">
            <AlertTriangle size={32} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">Click "Run Checks" to verify the system is ready.</p>
          </div>
        )}
        {loading && !checks && (
          <div className="text-center text-zinc-500 mt-16">
            <Loader size={20} className="mx-auto mb-3 animate-spin" />
            <p className="text-sm">Running preflight checks...</p>
          </div>
        )}
        {checks && checks.map((check, i) => (
          <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-zinc-900/30 border border-zinc-800/40">
            {statusIcon(check.status)}
            <div className="flex-1">
              <div className="text-sm text-zinc-200">{check.name}</div>
              {check.detail && (
                <div className="text-[10px] text-zinc-500 mt-0.5">{check.detail}</div>
              )}
            </div>
            <span className={statusBadge(check.status)}>{check.status}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
