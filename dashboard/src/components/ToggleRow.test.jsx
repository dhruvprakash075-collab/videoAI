import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ToggleRow from './ToggleRow.jsx';

describe('ToggleRow', () => {
  it('renders title and description', () => {
    render(<ToggleRow title="Notifications" description="Get pinged" value={false} onChange={() => {}} />);
    expect(screen.getByText('Notifications')).toBeInTheDocument();
    expect(screen.getByText('Get pinged')).toBeInTheDocument();
  });

  it('reflects the value as aria-pressed', () => {
    const { rerender } = render(<ToggleRow title="X" description="Y" value={false} onChange={() => {}} />);
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'false');
    rerender(<ToggleRow title="X" description="Y" value={true} onChange={() => {}} />);
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows "on" in the default aria-label when value is true', () => {
    render(<ToggleRow title="Subtitles" description="d" value={true} onChange={() => {}} />);
    expect(screen.getByRole('button')).toHaveAttribute('aria-label', 'Subtitles: on');
  });

  it('shows "off" in the default aria-label when value is false', () => {
    render(<ToggleRow title="Subtitles" description="d" value={false} onChange={() => {}} />);
    expect(screen.getByRole('button')).toHaveAttribute('aria-label', 'Subtitles: off');
  });

  it('uses a custom aria-label when provided', () => {
    render(<ToggleRow title="X" description="Y" value={true} onChange={() => {}} ariaLabel="Custom Aria" />);
    expect(screen.getByRole('button')).toHaveAttribute('aria-label', 'Custom Aria');
  });

  it('calls onChange with the toggled value when clicked', async () => {
    const onChange = vi.fn();
    render(<ToggleRow title="X" description="Y" value={false} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('calls onChange with false when clicked while value is true', async () => {
    const onChange = vi.fn();
    render(<ToggleRow title="X" description="Y" value={true} onChange={onChange} />);
    await userEvent.click(screen.getByRole('button'));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it('is a button (not a div) for keyboard accessibility', () => {
    render(<ToggleRow title="X" description="Y" value={false} onChange={() => {}} />);
    expect(screen.getByRole('button').tagName).toBe('BUTTON');
  });
});
