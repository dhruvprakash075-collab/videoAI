import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import VoiceCard from './VoiceCard.jsx';

describe('VoiceCard', () => {
  const voice = { name: 'lumian_lee', size: 5120 };

  it('renders the voice name', () => {
    render(<VoiceCard voice={voice} isPlaying={false} onPlay={() => {}} />);
    expect(screen.getByText('lumian_lee')).toBeInTheDocument();
  });

  it('renders the formatted size in KB', () => {
    render(<VoiceCard voice={voice} isPlaying={false} onPlay={() => {}} />);
    expect(screen.getByText('5.0 KB')).toBeInTheDocument();
  });

  it('calls onPlay with the voice name when the play button is clicked', async () => {
    const user = userEvent.setup();
    const onPlay = vi.fn();
    render(<VoiceCard voice={voice} isPlaying={false} onPlay={onPlay} />);
    await user.click(screen.getByRole('button', { name: /Preview voice lumian_lee/i }));
    expect(onPlay).toHaveBeenCalledWith('lumian_lee');
  });

  it('has a button role with accessible name including the voice name', () => {
    render(<VoiceCard voice={voice} isPlaying={false} onPlay={() => {}} />);
    const btn = screen.getByRole('button', { name: /Preview voice lumian_lee/i });
    expect(btn).toBeInTheDocument();
  });

  it('shows the play icon when not playing', () => {
    const { container } = render(<VoiceCard voice={voice} isPlaying={false} onPlay={() => {}} />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('shows the stop indicator (no svg icon) when isPlaying is true', () => {
    const { container } = render(<VoiceCard voice={voice} isPlaying={true} onPlay={() => {}} />);
    expect(container.querySelector('svg')).toBeNull();
    expect(container.querySelector('span.bg-emerald-500')).toBeInTheDocument();
  });
});
