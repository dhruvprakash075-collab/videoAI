import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ABPlayground from './ABPlayground.jsx';
import useABJob from '../hooks/useABJob.js';

vi.mock('../hooks/useABJob.js', () => ({ default: vi.fn() }));

const EMPTY_IMAGES = { a: [], b: [] };
const READY_IMAGES = { a: ['/a1.png'], b: ['/b1.png'] };

function mockUseABJob({ status = 'idle', images = EMPTY_IMAGES } = {}) {
  const start = vi.fn();
  const pick = vi.fn();
  useABJob.mockReturnValue({ status, images, start, pick });
  return { start, pick };
}

describe('ABPlayground', () => {
  beforeEach(() => {
    mockUseABJob();
  });

  it('renders the heading and a button to run A/B comparison', () => {
    render(<ABPlayground />);
    expect(screen.getByText(/A\/B Testing Studio/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run A\/B Comparison/i })).toBeInTheDocument();
  });

  it('renders two prompt textareas with default values', () => {
    render(<ABPlayground />);
    const textareas = screen.getAllByRole('textbox');
    expect(textareas).toHaveLength(2);
    expect(textareas[0].value).toMatch(/futuristic city/i);
    expect(textareas[1].value).toMatch(/raining/i);
  });

  it('calls start with the prompts when the run button is clicked', async () => {
    const { start } = mockUseABJob();
    const user = userEvent.setup();
    render(<ABPlayground />);
    await user.click(screen.getByRole('button', { name: /Run A\/B Comparison/i }));
    expect(start).toHaveBeenCalledWith(1, expect.stringMatching(/futuristic/), expect.stringMatching(/raining/));
  });

  it('disables the run button while running', () => {
    mockUseABJob({ status: 'running' });
    render(<ABPlayground />);
    expect(screen.getByRole('button', { name: /Generating Images/i })).toBeDisabled();
  });

  it('disables the run button while starting', () => {
    mockUseABJob({ status: 'starting' });
    render(<ABPlayground />);
    expect(screen.getByRole('button', { name: /Generating Images/i })).toBeDisabled();
  });

  it('does not render variant panels while there are no results', () => {
    render(<ABPlayground />);
    expect(screen.queryByText('Output A')).not.toBeInTheDocument();
    expect(screen.queryByText('Output B')).not.toBeInTheDocument();
  });

  it('renders variant panels after images arrive', () => {
    mockUseABJob({ status: 'ready', images: READY_IMAGES });
    render(<ABPlayground />);
    expect(screen.getByText('Output A')).toBeInTheDocument();
    expect(screen.getByText('Output B')).toBeInTheDocument();
  });

  it('forwards the commit click from variant panel to pick', async () => {
    const { pick } = mockUseABJob({ status: 'ready', images: READY_IMAGES });
    const user = userEvent.setup();
    render(<ABPlayground />);
    await user.click(screen.getByRole('button', { name: /Commit A/i }));
    expect(pick).toHaveBeenCalledWith('a', 1);
  });

  it('updates prompt A value when typed into', async () => {
    const user = userEvent.setup();
    render(<ABPlayground />);
    const a = screen.getAllByRole('textbox')[0];
    await user.clear(a);
    await user.type(a, 'NEW PROMPT');
    expect(a.value).toBe('NEW PROMPT');
  });
});
