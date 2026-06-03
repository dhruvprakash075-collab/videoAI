import { useState } from 'react';
import Sidebar from './components/Sidebar.jsx';
import Header from './components/Header.jsx';
import PreviewCanvas from './components/PreviewCanvas.jsx';
import VoiceManager from './components/VoiceManager.jsx';
import ABPlayground from './components/ABPlayground.jsx';
import StatusTracker from './components/StatusTracker.jsx';
import ConsultationModal from './components/ConsultationModal.jsx';
import SettingsDrawer from './components/SettingsDrawer.jsx';
import useStatusPolling from './hooks/useStatusPolling.js';
import useScriptUpload from './hooks/useScriptUpload.js';
import { apiSend } from './lib/api.js';

export default function App() {
  const [activeTab, setActiveTab] = useState('preview');
  const [showSettings, setShowSettings] = useState(false);
  const [status, setStatus] = useStatusPolling();
  const { inputRef: scriptInputRef, upload: uploadScript } = useScriptUpload();

  const handlePause = () => {
    apiSend('/api/manual_pause', new FormData()).catch(console.error);
  };

  const handleConsultationClose = () => {
    setStatus((prev) => ({ ...prev, state: 'running', active_question: null }));
  };

  const showConsultation = status.state === 'paused' && Boolean(status.active_question);

  return (
    <div className="flex h-screen w-full bg-[#0a0a0c] text-zinc-100 overflow-hidden font-sans selection:bg-zinc-800">
      <Sidebar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        settingsOpen={showSettings}
        onToggleSettings={() => setShowSettings((v) => !v)}
      />

      <div className="flex-1 flex flex-col relative transition-all duration-500 bg-[#0a0a0c]">
        <Header activeTab={activeTab} status={status} onPause={handlePause} />

        <main className="flex-1 overflow-auto p-8 relative">
          {activeTab === 'preview' && (
            <PreviewCanvas
              video={status.video}
              scriptInputRef={scriptInputRef}
              onScriptPicked={uploadScript}
            />
          )}
          {activeTab === 'voices' && <VoiceManager />}
          {activeTab === 'ab-testing' && <ABPlayground />}
        </main>

        <div className="absolute bottom-6 right-8 w-96">
          <StatusTracker logs={status.logs} currentState={status.state} />
        </div>
      </div>

      {showConsultation && (
        <ConsultationModal
          question={status.active_question}
          onClose={handleConsultationClose}
        />
      )}

      <SettingsDrawer open={showSettings} onClose={() => setShowSettings(false)} />
    </div>
  );
}
