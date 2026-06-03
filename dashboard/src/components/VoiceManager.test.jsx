import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import VoiceManager from './VoiceManager.jsx';
import useVoices from '../hooks/useVoices.js';
import useVoicePlayer from '../hooks/useVoicePlayer.js';

vi.mock('../hooks/useVoices.js', () => ({ default: vi.fn() }));
vi.mock('../hooks/useVoicePlayer.js', () => ({ default: vi.fn() }));

describe('VoiceManager', () => {
  let refreshMock;
  let playMock;

  beforeEach(() => {
    refreshMock = vi.fn();
    playMock = vi.fn();
    useVoices.mockReturnValue({ voices: [], refresh: refreshMock });
    useVoicePlayer.mockReturnValue({ playingVoice: null, play: playMock });
  });

  it('renders the Voice Studio header and the upload zone', () => {
    render(<VoiceManager />);
    expect(screen.getByText('Voice Studio')).toBeInTheDocument();
    expect(screen.getByText(/Upload New Voice/i)).toBeInTheDocument();
  });

  it('shows the empty state when there are no voices', () => {
    render(<VoiceManager />);
    expect(screen.getByText(/No voices uploaded yet/i)).toBeInTheDocument();
  });

  it('renders a card for each voice returned by useVoices', () => {
    useVoices.mockReturnValue({
      voices: [
        { name: 'alice', size: 2048 },
        { name: 'bob',   size: 4096 },
      ],
      refresh: refreshMock,
    });
    render(<VoiceManager />);
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
    expect(screen.queryByText(/No voices uploaded yet/i)).not.toBeInTheDocument();
  });

  it('calls play with the clicked voice name', async () => {
    useVoices.mockReturnValue({
      voices: [{ name: 'alice', size: 2048 }],
      refresh: refreshMock,
    });
    const user = userEvent.setup();
    render(<VoiceManager />);
    await user.click(screen.getByRole('button', { name: /Preview voice alice/i }));
    expect(playMock).toHaveBeenCalledWith('alice');
  });

  it('refetches voices when the upload zone reports onUploaded', async () => {
    const user = userEvent.setup();
    render(<VoiceManager />);
    const nameInput = screen.getByPlaceholderText('e.g. lumian_lee');
    await user.type(nameInput, 'x');
    expect(refreshMock).not.toHaveBeenCalled();
  });

  it('keeps both panels mounted when both have content', () => {
    useVoices.mockReturnValue({
      voices: [{ name: 'a', size: 1000 }],
      refresh: refreshMock,
    });
    render(<VoiceManager />);
    expect(screen.getByText('Voice Library')).toBeInTheDocument();
    expect(screen.getByText('a')).toBeInTheDocument();
  });
});
