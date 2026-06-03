import { Settings, Play, Mic, Image as ImageIcon } from 'lucide-react';

const TABS = [
  { id: 'preview',    label: 'Director Canvas', icon: Play },
  { id: 'voices',     label: 'Voice Studio',    icon: Mic },
  { id: 'ab-testing', label: 'A/B Testing',     icon: ImageIcon },
];

function SidebarButton({ active, onClick, title, children }) {
  const base = 'p-2.5 rounded-xl transition-all duration-300';
  const tone = active
    ? 'bg-zinc-800/80 text-white shadow-sm'
    : 'text-zinc-500 hover:text-zinc-300';
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-label={title}
      aria-pressed={active}
      className={`${base} ${tone}`}
    >
      {children}
    </button>
  );
}

export default function Sidebar({ activeTab, onTabChange, onToggleSettings, settingsOpen }) {
  return (
    <div className="w-16 border-r border-zinc-800/40 bg-[#0a0a0c] flex flex-col items-center py-6 gap-8 z-20">
      <div className="w-8 h-8 flex items-center justify-center font-serif italic text-2xl text-white mb-2">
        V
      </div>

      <div className="flex flex-col gap-4">
        {TABS.map(({ id, label, icon: Icon }) => (
          <SidebarButton
            key={id}
            active={activeTab === id}
            onClick={() => onTabChange(id)}
            title={label}
          >
            <Icon size={18} strokeWidth={2} />
          </SidebarButton>
        ))}
      </div>

      <div className="mt-auto">
        <SidebarButton
          active={settingsOpen}
          onClick={onToggleSettings}
          title="Settings"
        >
          <Settings size={18} strokeWidth={2} />
        </SidebarButton>
      </div>
    </div>
  );
}
