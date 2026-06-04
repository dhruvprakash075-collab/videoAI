import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import UploadZone from './UploadZone.jsx';
import { apiSend } from '../lib/api.js';

vi.mock('../lib/api.js', () => ({
  apiSend: vi.fn(),
}));

function makeFile(name = 'sample.wav', type = 'audio/wav', size = 1024) {
  const file = new File(['x'.repeat(size)], name, { type });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

function renderZone(props = {}) {
  const onUploaded = props.onUploaded ?? vi.fn().mockResolvedValue(undefined);
  const onCharacterNameChange = props.onCharacterNameChange ?? vi.fn();
  render(
    <UploadZone
      characterName={props.characterName ?? 'alice'}
      onCharacterNameChange={onCharacterNameChange}
      onUploaded={onUploaded}
    />
  );
  return { onUploaded, onCharacterNameChange, fileInput: document.querySelector('input[type="file"]') };
}

function pickFile(fileInput, file) {
  fireEvent.change(fileInput, { target: { files: [file] } });
}

describe('UploadZone', () => {
  beforeEach(() => {
    apiSend.mockReset();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  it('renders the character name input with placeholder', () => {
    render(<UploadZone characterName="" onCharacterNameChange={vi.fn()} onUploaded={vi.fn()} />);
    expect(screen.getByPlaceholderText('e.g. lumian_lee')).toBeInTheDocument();
  });

  it('renders the drop zone and instruction text', () => {
    const { onCharacterNameChange, onUploaded } = renderZone({ characterName: 'bob' });
    expect(screen.getByText(/Drop raw voice sample here/i)).toBeInTheDocument();
    expect(screen.getByText(/WAV or MP3 up to 10MB/i)).toBeInTheDocument();
    expect(onCharacterNameChange).toBeDefined();
    expect(onUploaded).toBeDefined();
  });

  it('calls onCharacterNameChange when typing in the name field', async () => {
    const { onCharacterNameChange } = renderZone({ characterName: '' });
    const input = screen.getByPlaceholderText('e.g. lumian_lee');
    await (await import('@testing-library/user-event')).default.setup().type(input, 'alice');
    expect(onCharacterNameChange).toHaveBeenCalled();
    expect(onCharacterNameChange.mock.calls[0][0]).toBe('a');
  });

  it('alerts and does not upload when character name is empty', async () => {
    apiSend.mockResolvedValue({ ok: true });
    const { fileInput, onUploaded } = renderZone({ characterName: '   ' });
    pickFile(fileInput, makeFile());
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith('Please enter a character name first.');
    });
    expect(apiSend).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('uploads the file, calls onUploaded, and clears the name on success', async () => {
    apiSend.mockResolvedValue({ ok: true });
    const { fileInput, onUploaded, onCharacterNameChange } = renderZone();
    pickFile(fileInput, makeFile());
    await waitFor(() => {
      expect(apiSend).toHaveBeenCalledWith('/api/upload_voice', expect.any(FormData));
    });
    await waitFor(() => {
      expect(onUploaded).toHaveBeenCalled();
    });
    expect(onCharacterNameChange).toHaveBeenCalledWith('');
  });

  it('alerts on upload failure', async () => {
    apiSend.mockResolvedValue({ ok: false });
    const { fileInput, onUploaded } = renderZone();
    pickFile(fileInput, makeFile());
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith('Upload failed.');
    });
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('alerts on thrown error from apiSend', async () => {
    apiSend.mockRejectedValue(new Error('network down'));
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { fileInput } = renderZone();
    pickFile(fileInput, makeFile());
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith('Upload error.');
    });
    errSpy.mockRestore();
  });

  it('toggles isDragging styles on dragenter and dragleave', () => {
    const { container } = render(
      <UploadZone characterName="alice" onCharacterNameChange={vi.fn()} onUploaded={vi.fn()} />
    );
    const dropZone = container.querySelector('.aspect-video');
    fireEvent.dragOver(dropZone);
    expect(dropZone.className).toMatch(/border-emerald-500/);
    fireEvent.dragLeave(dropZone);
    expect(dropZone.className).not.toMatch(/border-emerald-500/);
  });

  it('alerts when dropped file fails voice validation', () => {
    const { onUploaded } = renderZone();
    const dropZone = document.querySelector('.aspect-video');
    const badFile = new File(['x'], 'sample.txt', { type: 'text/plain' });
    Object.defineProperty(badFile, 'size', { value: 1024 });
    fireEvent.drop(dropZone, { dataTransfer: { files: [badFile] } });
    expect(window.alert).toHaveBeenCalled();
    expect(apiSend).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });
});
