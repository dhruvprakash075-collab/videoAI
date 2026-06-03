import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConsultationModal from './ConsultationModal.jsx';

describe('ConsultationModal', () => {
  let onClose;
  beforeEach(() => {
    onClose = vi.fn();
    global.fetch = vi.fn();
    global.alert = vi.fn();
  });

  it('renders the question', () => {
    render(<ConsultationModal question="Continue with horror theme?" onClose={onClose} />);
    expect(screen.getByText('Continue with horror theme?')).toBeInTheDocument();
  });

  it('renders a Director Paused heading', () => {
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    expect(screen.getByText('Director Paused')).toBeInTheDocument();
  });

  it('renders a textarea for the reply', () => {
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    expect(screen.getByPlaceholderText(/creative direction/i)).toBeInTheDocument();
  });

  it('renders a Send & Resume Engine button', () => {
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    expect(screen.getByRole('button', { name: /Send & Resume/i })).toBeInTheDocument();
  });

  it('does not POST when the reply is empty', async () => {
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    await userEvent.click(screen.getByRole('button', { name: /Send & Resume/i }));
    expect(global.fetch).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does not POST when the reply is only whitespace', async () => {
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    const textarea = screen.getByPlaceholderText(/creative direction/i);
    await userEvent.type(textarea, '   ');
    await userEvent.click(screen.getByRole('button', { name: /Send & Resume/i }));
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('POSTs the reply to /api/consultation_reply on submit', async () => {
    global.fetch.mockResolvedValue({ ok: true });
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    const textarea = screen.getByPlaceholderText(/creative direction/i);
    await userEvent.type(textarea, 'try a different angle');
    await userEvent.click(screen.getByRole('button', { name: /Send & Resume/i }));

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/consultation_reply',
      expect.objectContaining({ method: 'POST' })
    );
    const form = global.fetch.mock.calls[0][1].body;
    expect(form.get('reply')).toBe('try a different angle');
  });

  it('calls onClose after a successful submit', async () => {
    global.fetch.mockResolvedValue({ ok: true });
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    const textarea = screen.getByPlaceholderText(/creative direction/i);
    await userEvent.type(textarea, 'my reply');
    await userEvent.click(screen.getByRole('button', { name: /Send & Resume/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('clears the textarea after a successful submit', async () => {
    global.fetch.mockResolvedValue({ ok: true });
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    const textarea = screen.getByPlaceholderText(/creative direction/i);
    await userEvent.type(textarea, 'my reply');
    await userEvent.click(screen.getByRole('button', { name: /Send & Resume/i }));
    expect(textarea.value).toBe('');
  });

  it('does NOT call onClose on a failed submit', async () => {
    global.fetch.mockResolvedValue({ ok: false, status: 500 });
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    await userEvent.type(screen.getByPlaceholderText(/creative direction/i), 'reply');
    await userEvent.click(screen.getByRole('button', { name: /Send & Resume/i }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does NOT call onClose on a network error', async () => {
    global.fetch.mockRejectedValue(new Error('net'));
    render(<ConsultationModal question="Q?" onClose={onClose} />);
    await userEvent.type(screen.getByPlaceholderText(/creative direction/i), 'reply');
    await userEvent.click(screen.getByRole('button', { name: /Send & Resume/i }));
    expect(onClose).not.toHaveBeenCalled();
  });
});
