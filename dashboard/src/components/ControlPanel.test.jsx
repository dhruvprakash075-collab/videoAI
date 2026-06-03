import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ControlPanel from './ControlPanel.jsx';
import { API_BASE } from '../lib/api.js';

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe('ControlPanel', () => {
  let onClose;
  let fetchMock;

  beforeEach(() => {
    onClose = vi.fn();
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    });
    global.fetch = fetchMock;
    vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders the System Config header and all section titles', () => {
    render(<ControlPanel onClose={onClose} />);
    expect(screen.getByText('System Config')).toBeInTheDocument();
    expect(screen.getByText('Voice Engine')).toBeInTheDocument();
    expect(screen.getByText('Visual Generation')).toBeInTheDocument();
    expect(screen.getByText('Post-Production')).toBeInTheDocument();
  });

  it('renders both voice engine buttons with the active one pressed', () => {
    render(<ControlPanel onClose={onClose} />);
    const omnivoice = screen.getByRole('button', { name: /OmniVoice/i });
    const edge = screen.getByRole('button', { name: /Edge TTS/i });
    expect(omnivoice).toHaveAttribute('aria-pressed', 'true');
    expect(edge).toHaveAttribute('aria-pressed', 'false');
  });

  it('switches the active engine when a different engine is clicked', async () => {
    const user = userEvent.setup();
    render(<ControlPanel onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: /Edge TTS/i }));
    expect(screen.getByRole('button', { name: /Edge TTS/i })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /OmniVoice/i })).toHaveAttribute('aria-pressed', 'false');
  });

  it('loads config from /api/config on mount', async () => {
    fetchMock.mockResolvedValue({
      json: () => Promise.resolve({ voiceEngine: 'edge', dynamicSubtitles: false, uncappedScaling: true, maxImagesPerSegment: 9 }),
    });
    render(<ControlPanel onClose={onClose} />);
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(`${API_BASE}/api/config`, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Edge TTS/i })).toHaveAttribute('aria-pressed', 'true');
    });
  });

  it('ignores config response that includes a "status" key (error payload)', async () => {
    fetchMock.mockResolvedValue({
      json: () => Promise.resolve({ status: 'error', message: 'nope' }),
    });
    render(<ControlPanel onClose={onClose} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: /OmniVoice/i })).toHaveAttribute('aria-pressed', 'true');
  });

  it('logs (but does not throw) when the config fetch fails with a non-Abort error', async () => {
    const err = new Error('boom');
    err.name = 'NetworkError';
    fetchMock.mockRejectedValue(err);
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ControlPanel onClose={onClose} />);
    await waitFor(() => {
      expect(errSpy).toHaveBeenCalledWith('Failed to load configuration:', err);
    });
    errSpy.mockRestore();
  });

  it('does not log when the config fetch is aborted on unmount', async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { unmount } = render(<ControlPanel onClose={onClose} />);
    unmount();
    await flush();
    expect(errSpy).not.toHaveBeenCalled();
    errSpy.mockRestore();
  });

  it('toggles "Uncapped Scaling" via the toggle row', async () => {
    const user = userEvent.setup();
    render(<ControlPanel onClose={onClose} />);
    const toggle = screen.getByRole('button', { name: /Uncapped Scaling/i });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });

  it('hides the Images Per Segment slider when uncapped scaling is on', async () => {
    const user = userEvent.setup();
    render(<ControlPanel onClose={onClose} />);
    expect(screen.getByRole('slider', { name: /Images per segment/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Uncapped Scaling/i }));
    expect(screen.queryByRole('slider', { name: /Images per segment/i })).not.toBeInTheDocument();
  });

  it('updates maxImagesPerSegment when the slider is moved', async () => {
    render(<ControlPanel onClose={onClose} />);
    const slider = screen.getByRole('slider', { name: /Images per segment/i });
    expect(slider).toHaveValue('6');
    fireEvent.change(slider, { target: { value: '11' } });
    expect(screen.getByText('11')).toBeInTheDocument();
  });

  it('calls onClose and POSTs the config on save success', async () => {
    fetchMock.mockImplementation((url, opts) => {
      if (!opts || !opts.method) {
        return Promise.resolve({ json: () => Promise.resolve({}) });
      }
      return Promise.resolve({ json: () => Promise.resolve({ status: 'success' }) });
    });
    const user = userEvent.setup();
    render(<ControlPanel onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: /Save Configuration/i }));
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('alerts when the save response reports a non-success status', async () => {
    fetchMock.mockImplementation((url, opts) => {
      if (!opts || !opts.method) {
        return Promise.resolve({ json: () => Promise.resolve({}) });
      }
      return Promise.resolve({ json: () => Promise.resolve({ status: 'error', message: 'bad config' }) });
    });
    const user = userEvent.setup();
    render(<ControlPanel onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: /Save Configuration/i }));
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/Failed to save settings/));
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('alerts on thrown save error and does not call onClose', async () => {
    fetchMock.mockImplementation((url, opts) => {
      if (!opts || !opts.method) {
        return Promise.resolve({ json: () => Promise.resolve({}) });
      }
      return Promise.reject(new Error('save blew up'));
    });
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const user = userEvent.setup();
    render(<ControlPanel onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: /Save Configuration/i }));
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith('Save failed: save blew up');
    });
    expect(onClose).not.toHaveBeenCalled();
    errSpy.mockRestore();
  });

  it('calls onClose when the close (X) button is clicked', async () => {
    const user = userEvent.setup();
    render(<ControlPanel onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: /Close settings/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
