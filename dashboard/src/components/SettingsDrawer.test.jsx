import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SettingsDrawer from './SettingsDrawer.jsx';

vi.mock('./ControlPanel.jsx', () => ({
  default: ({ onClose }) => (
    <div data-testid="control-panel">
      <button onClick={onClose}>close-control</button>
    </div>
  ),
}));

describe('SettingsDrawer', () => {
  it('renders ControlPanel when open', () => {
    render(<SettingsDrawer open={true} onClose={() => {}} />);
    expect(screen.getByTestId('control-panel')).toBeInTheDocument();
  });

  it('renders ControlPanel even when closed (keeps it mounted for animation)', () => {
    render(<SettingsDrawer open={false} onClose={() => {}} />);
    expect(screen.getByTestId('control-panel')).toBeInTheDocument();
  });

  it('renders a backdrop overlay when open', () => {
    render(<SettingsDrawer open={true} onClose={() => {}} />);
    expect(document.querySelector('.backdrop-blur-\\[2px\\]')).toBeInTheDocument();
  });

  it('does not render the backdrop overlay when closed', () => {
    render(<SettingsDrawer open={false} onClose={() => {}} />);
    expect(document.querySelector('.backdrop-blur-\\[2px\\]')).not.toBeInTheDocument();
  });

  it('calls onClose when the backdrop is clicked', async () => {
    const onClose = vi.fn();
    render(<SettingsDrawer open={true} onClose={onClose} />);
    await userEvent.click(document.querySelector('.backdrop-blur-\\[2px\\]'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose when ControlPanel requests close', async () => {
    const onClose = vi.fn();
    render(<SettingsDrawer open={true} onClose={onClose} />);
    await userEvent.click(screen.getByText('close-control'));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
