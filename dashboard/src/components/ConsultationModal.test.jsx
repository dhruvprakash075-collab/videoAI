import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConsultationModal from './ConsultationModal.jsx';

const QUESTION = 'Q?';
const REPLY = 'try a different angle';

function renderModal({ question = QUESTION, onClose = vi.fn() } = {}) {
  render(<ConsultationModal question={question} onClose={onClose} />);
  return {
    onClose,
    textarea: screen.getByPlaceholderText(/creative direction/i),
    submit: screen.getByRole('button', { name: /Send & Resume/i }),
  };
}

async function typeAndSubmit(textarea, submit, reply) {
  await userEvent.type(textarea, reply);
  await userEvent.click(submit);
}

describe('ConsultationModal', () => {
  let onClose;
  beforeEach(() => {
    onClose = vi.fn();
    global.fetch = vi.fn();
    global.alert = vi.fn();
  });

  it('renders the question', () => {
    renderModal({ question: 'Continue with horror theme?' });
    expect(screen.getByText('Continue with horror theme?')).toBeInTheDocument();
  });

  it('renders a Director Paused heading', () => {
    renderModal();
    expect(screen.getByText('Director Paused')).toBeInTheDocument();
  });

  it('renders a textarea for the reply', () => {
    renderModal();
    expect(screen.getByPlaceholderText(/creative direction/i)).toBeInTheDocument();
  });

  it('renders a Send & Resume Engine button', () => {
    renderModal();
    expect(screen.getByRole('button', { name: /Send & Resume/i })).toBeInTheDocument();
  });

  it('does not POST when the reply is empty', async () => {
    const { submit } = renderModal({ onClose });
    await userEvent.click(submit);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does not POST when the reply is only whitespace', async () => {
    const { textarea, submit } = renderModal({ onClose });
    await userEvent.type(textarea, '   ');
    await userEvent.click(submit);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('POSTs the reply to /api/consultation_reply on submit', async () => {
    global.fetch.mockResolvedValue({ ok: true });
    const { textarea, submit } = renderModal({ onClose });
    await typeAndSubmit(textarea, submit, REPLY);

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/consultation_reply',
      expect.objectContaining({ method: 'POST' })
    );
    const form = global.fetch.mock.calls[0][1].body;
    expect(form.get('reply')).toBe(REPLY);
  });

  it('calls onClose after a successful submit', async () => {
    global.fetch.mockResolvedValue({ ok: true });
    const { textarea, submit } = renderModal({ onClose });
    await typeAndSubmit(textarea, submit, 'my reply');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('clears the textarea after a successful submit', async () => {
    global.fetch.mockResolvedValue({ ok: true });
    const { textarea, submit } = renderModal({ onClose });
    await typeAndSubmit(textarea, submit, 'my reply');
    expect(textarea.value).toBe('');
  });

  it('does NOT call onClose on a failed submit', async () => {
    global.fetch.mockResolvedValue({ ok: false, status: 500 });
    const { textarea, submit } = renderModal({ onClose });
    await typeAndSubmit(textarea, submit, 'reply');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('does NOT call onClose on a network error', async () => {
    global.fetch.mockRejectedValue(new Error('net'));
    const { textarea, submit } = renderModal({ onClose });
    await typeAndSubmit(textarea, submit, 'reply');
    expect(onClose).not.toHaveBeenCalled();
  });
});
