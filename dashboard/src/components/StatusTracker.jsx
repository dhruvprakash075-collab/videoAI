import { Activity, ChevronUp, ChevronDown } from 'lucide-react';
import { useState, useRef, useEffect } from 'react';

export default function StatusTracker({ logs, currentState }) {
  const [expanded, setExpanded] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    // P3-21: only auto-scroll when the user is already pinned to the bottom
    // (within 50px), so they can freely scroll back through logs during a run.
    if (scrollRef.current) {
      const el = scrollRef.current;
      const isPinnedToBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 50;
      if (isPinnedToBottom) {
        el.scrollTop = el.scrollHeight;
      }
    }
  }, [logs, expanded]);

  const latestLog = logs && logs.length > 0 ? logs[logs.length - 1] : "Engine Idle";

  return (
    <div className="bg-[#0f0f13]/90 backdrop-blur-xl border border-zinc-800/50 rounded-2xl overflow-hidden shadow-2xl transition-all duration-500 ease-[cubic-bezier(0.2,0.8,0.2,1)]">
      <div 
        className="px-5 py-4 flex items-center justify-between cursor-pointer hover:bg-zinc-800/20 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-4">
          <Activity size={14} className={currentState === 'running' ? 'text-zinc-200 animate-pulse' : 'text-zinc-600'} />
          <span className="text-xs font-mono text-zinc-400 truncate max-w-[260px]">
             {latestLog}
          </span>
        </div>
        {expanded ? <ChevronDown size={14} className="text-zinc-500" /> : <ChevronUp size={14} className="text-zinc-500" />}
      </div>
      
      <div 
        ref={scrollRef}
        className={`overflow-y-auto px-5 py-3 border-t border-zinc-800/30 bg-[#0a0a0c]/50 transition-all duration-500 ${expanded ? 'h-64 opacity-100' : 'h-0 py-0 opacity-0 border-0'}`}
      >
        {logs && logs.length > 0 ? logs.map((log, idx) => (
          <div key={idx} className="text-[10px] font-mono text-zinc-500 py-1.5 flex gap-3">
            <span className="opacity-30 shrink-0">{idx.toString().padStart(3, '0')}</span> 
            <span className="break-words">{log}</span>
          </div>
        )) : (
          <div className="text-[10px] font-mono text-zinc-700 py-2">No logs available.</div>
        )}
      </div>
    </div>
  );
}
