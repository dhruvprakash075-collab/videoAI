import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ABPlayground from './ABPlayground.jsx';
import useABJob from '../hooks/useABJob.js';

vi.mock('../hooks/useABJob.js', () => ({ default: vi.fn() }));

describe('ABPlayground', () => {
  let startMock;
  let pickMock;

  beforeEach(() => {
    startMock = vi.fn();
    pickMock = vi.fn();
    useABJob.mockReturnValue({
      status: 'idle',
      images: { a: [], b: [] },
      start: startMock,
      pick: pickMock,
    });
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
    const user = userEvent.setup();
    render(<ABPlayground />);
    await user.click(screen.getByRole('button', { name: /Run A\/B Comparison/i }));
    expect(startMock).toHaveBeenCalledWith(1, expect.stringMatching(/futuristic/), expect.stringMatching(/raining/));
  });

  it('disables the run button while running', () => {
    useABJob.mockReturnValue({
      status: 'running',
      images: { a: [], b: [] },
      start: startMock,
      pick: pickMock,
    });
    render(<ABPlayground />);
    expect(screen.getByRole('button', { name: /Generating Images/i })).toBeDisabled();
  });

  it('disables the run button while starting', () => {
    useABJob.mockReturnValue({
      status: 'starting',
      images: { a: [], b: [] },
      start: startMock,
      pick: pickMock,
    });
    render(<ABPlayground />);
    expect(screen.getByRole('button', { name: /Generating Images/i })).toBeDisabled();
  });

  it('does not render variant panels while there are no results', () => {
    render(<ABPlayground />);
    expect(screen.queryByText('Output A')).not.toBeInTheDocument();
    expect(screen.queryByText('Output B')).not.toBeInTheDocument();
  });

  it('renders variant panels after images arrive', () => {
    useABJob.mockReturnValue({
      status: 'ready',
      images: { a: ['/a1.png'], b: ['/b1.png'] },
      start: startMock,
      pick: pickMock,
    });
    render(<ABPlayground />);
    expect(screen.getByText('Output A')).toBeInTheDocument();
    expect(screen.getByText('Output B')).toBeInTheDocument();
  });

  it('forwards the commit click from variant panel to pick', async () => {
    const user = userEvent.setup();
    useABJob.mockReturnValue({
      status: 'ready',
      images: { a: ['/a1.png'], b: ['/b1.png'] },
      start: startMock,
      pick: pickMock,
    });
    render(<ABPlayground />);
    await user.click(screen.getByRole('button', { name: /Commit A/i }));
    expect(pickMock).toHaveBeenCalledWith('a', 1);
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
