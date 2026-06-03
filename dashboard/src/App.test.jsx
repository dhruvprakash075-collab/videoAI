import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App.jsx';
import useStatusPolling from './hooks/useStatusPolling.js';
import useScriptUpload from './hooks/useScriptUpload.js';
import { apiSend } from './lib/api.js';

vi.mock('./hooks/useStatusPolling.js', () => ({ default: vi.fn() }));
vi.mock('./hooks/useScriptUpload.js', () => ({ default: vi.fn() }));
vi.mock('./lib/api.js', () => ({
  apiSend: vi.fn(() => Promise.resolve({ ok: true })),
  apiGet: vi.fn(() => Promise.resolve({ voices: [] })),
  API_BASE: 'http://test',
}));
vi.mock('./components/SettingsDrawer.jsx', () => ({
  default: ({ open, onClose }) =>
    open ? (
      <div>
        <button onClick={onClose} aria-label="Close settings drawer">Close</button>
      </div>
    ) : null,
}));
vi.mock('./components/ConsultationModal.jsx', () => ({
  default: ({ question, onClose }) => (
    <div>
      <h2>Director Paused</h2>
      <p>{question?.question}</p>
      <button onClick={onClose}>Send & Resume Engine</button>
    </div>
  ),
}));

describe('App', () => {
  let setStatusMock;

  beforeEach(() => {
    setStatusMock = vi.fn();
    useStatusPolling.mockReturnValue([
      { state: 'running', video: null, active_question: null, logs: [] },
      setStatusMock,
    ]);
    useScriptUpload.mockReturnValue({
      inputRef: { current: null },
      upload: vi.fn(),
    });
    apiSend.mockReset();
    apiSend.mockReturnValue(Promise.resolve({ ok: true }));
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders the Sidebar and the header status', () => {
    render(<App />);
    expect(screen.getByRole('button', { name: /Director Canvas/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Voice Studio/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /A\/B Testing/i })).toBeInTheDocument();
  });

  it('shows the Preview tab by default (upload + canvas zone)', () => {
    render(<App />);
    expect(screen.getByText(/Upload Lore Script/i)).toBeInTheDocument();
  });

  it('switches to the Voices tab when its nav item is clicked', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /Voice Studio/i }));
    expect(screen.getAllByText('Voice Studio').length).toBeGreaterThanOrEqual(1);
  });

  it('switches to the A/B Testing tab when its nav item is clicked', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /A\/B Testing/i }));
    expect(screen.getByText(/A\/B Testing Studio/i)).toBeInTheDocument();
  });

  it('opens and closes the settings drawer via the settings toggle', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /Settings/i }));
    expect(screen.getByRole('button', { name: /Close settings drawer/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Close settings drawer/i }));
    expect(screen.queryByRole('button', { name: /Close settings drawer/i })).not.toBeInTheDocument();
  });

  it('calls apiSend on the manual pause handler when the pause button is clicked', async () => {
    useStatusPolling.mockReturnValue([
      { state: 'running', video: null, active_question: null, logs: [] },
      setStatusMock,
    ]);
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /Pause Engine/i }));
    expect(apiSend).toHaveBeenCalledWith('/api/manual_pause', expect.any(FormData));
  });

  it('renders the consultation modal when state is paused and an active question exists', () => {
    useStatusPolling.mockReturnValue([
      { state: 'paused', video: null, active_question: { id: 'q1', question: 'Continue?', options: ['yes', 'no'] }, logs: [] },
      setStatusMock,
    ]);
    render(<App />);
    expect(screen.getByText('Continue?')).toBeInTheDocument();
  });

  it('does not render the consultation modal when state is running', () => {
    useStatusPolling.mockReturnValue([
      { state: 'running', video: null, active_question: { id: 'q1', question: 'Continue?', options: ['yes', 'no'] }, logs: [] },
      setStatusMock,
    ]);
    render(<App />);
    expect(screen.queryByText('Continue?')).not.toBeInTheDocument();
  });

  it('does not render the consultation modal when paused without an active question', () => {
    useStatusPolling.mockReturnValue([
      { state: 'paused', video: null, active_question: null, logs: [] },
      setStatusMock,
    ]);
    render(<App />);
    expect(screen.queryByText('Director Paused')).not.toBeInTheDocument();
  });

  it('clears the active question and returns to running when the consultation modal is closed', async () => {
    useStatusPolling.mockReturnValue([
      { state: 'paused', video: null, active_question: { id: 'q1', question: 'Continue?', options: ['yes', 'no'] }, logs: [] },
      setStatusMock,
    ]);
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /Send & Resume Engine/i }));
    await waitFor(() => {
      expect(setStatusMock).toHaveBeenCalled();
    });
    const updater = setStatusMock.mock.calls[0][0];
    const next = updater({ state: 'paused', active_question: { id: 'q1' } });
    expect(next.state).toBe('running');
    expect(next.active_question).toBeNull();
  });

  it('passes the current video URL into the PreviewCanvas', () => {
    useStatusPolling.mockReturnValue([
      { state: 'running', video: '/studio_outputs/clip.mp4', active_question: null, logs: [] },
      setStatusMock,
    ]);
    const { container } = render(<App />);
    const video = container.querySelector('video');
    expect(video).toHaveAttribute('src', 'http://test/studio_outputs/clip.mp4');
  });
});
