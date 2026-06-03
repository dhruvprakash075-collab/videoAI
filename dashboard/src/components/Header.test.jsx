import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Header from './Header.jsx';

const IDLE_STATUS = { state: 'idle', logs: [], video: null, active_question: null };

describe('Header', () => {
  it('shows the correct title for each tab', () => {
    const { rerender } = render(<Header activeTab="preview" status={IDLE_STATUS} onPause={() => {}} />);
    expect(screen.getByRole('heading')).toHaveTextContent('Director Canvas');
    rerender(<Header activeTab="voices" status={IDLE_STATUS} onPause={() => {}} />);
    expect(screen.getByRole('heading')).toHaveTextContent('Voice Studio');
    rerender(<Header activeTab="ab-testing" status={IDLE_STATUS} onPause={() => {}} />);
    expect(screen.getByRole('heading')).toHaveTextContent('A/B Testing');
  });

  it('shows the pipeline status text', () => {
    render(<Header activeTab="preview" status={{ ...IDLE_STATUS, state: 'running' }} onPause={() => {}} />);
    expect(screen.getByText('running')).toBeInTheDocument();
  });

  it('does NOT show the Pause button when state is not running', () => {
    render(<Header activeTab="preview" status={{ ...IDLE_STATUS, state: 'idle' }} onPause={() => {}} />);
    expect(screen.queryByRole('button', { name: 'Pause Engine' })).not.toBeInTheDocument();
  });

  it('shows the Pause button only when state is running', () => {
    render(<Header activeTab="preview" status={{ ...IDLE_STATUS, state: 'running' }} onPause={() => {}} />);
    expect(screen.getByRole('button', { name: 'Pause Engine' })).toBeInTheDocument();
  });

  it('calls onPause when the Pause button is clicked', async () => {
    const onPause = vi.fn();
    render(<Header activeTab="preview" status={{ ...IDLE_STATUS, state: 'running' }} onPause={onPause} />);
    await userEvent.click(screen.getByRole('button', { name: 'Pause Engine' }));
    expect(onPause).toHaveBeenCalledTimes(1);
  });
});
