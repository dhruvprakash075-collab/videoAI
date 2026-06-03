import ControlPanel from './ControlPanel.jsx';

export default function SettingsDrawer({ open, onClose }) {
  return (
    <>
      <div
        className={`absolute top-0 right-0 h-full w-96 bg-[#0a0a0c] border-l border-zinc-800/50 shadow-2xl transition-transform duration-500 ease-[cubic-bezier(0.2,0.8,0.2,1)] z-40 ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <ControlPanel onClose={onClose} />
      </div>

      {open && (
        <div
          className="absolute inset-0 bg-black/40 backdrop-blur-[2px] z-30 transition-opacity duration-500 cursor-pointer"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
    </>
  );
}
