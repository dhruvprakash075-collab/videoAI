import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

describe('UploadZone', () => {
  let onUploaded;
  let onCharacterNameChange;

  beforeEach(() => {
    onUploaded = vi.fn().mockResolvedValue(undefined);
    onCharacterNameChange = vi.fn();
    apiSend.mockReset();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  it('renders the character name input with placeholder', () => {
    render(
      <UploadZone
        characterName=""
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    const input = screen.getByPlaceholderText('e.g. lumian_lee');
    expect(input).toBeInTheDocument();
  });

  it('renders the drop zone and instruction text', () => {
    render(
      <UploadZone
        characterName="bob"
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    expect(screen.getByText(/Drop raw voice sample here/i)).toBeInTheDocument();
    expect(screen.getByText(/WAV or MP3 up to 10MB/i)).toBeInTheDocument();
  });

  it('calls onCharacterNameChange when typing in the name field', async () => {
    const user = userEvent.setup();
    render(
      <UploadZone
        characterName=""
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    await user.type(screen.getByPlaceholderText('e.g. lumian_lee'), 'alice');
    expect(onCharacterNameChange).toHaveBeenCalled();
    expect(onCharacterNameChange.mock.calls[0][0]).toBe('a');
  });

  it('alerts and does not upload when character name is empty', async () => {
    apiSend.mockResolvedValue({ ok: true });
    render(
      <UploadZone
        characterName="   "
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    const fileInput = document.querySelector('input[type="file"]');
    const file = makeFile();
    fireEvent.change(fileInput, { target: { files: [file] } });
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith('Please enter a character name first.');
    });
    expect(apiSend).not.toHaveBeenCalled();
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('uploads the file, calls onUploaded, and clears the name on success', async () => {
    apiSend.mockResolvedValue({ ok: true });
    render(
      <UploadZone
        characterName="alice"
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    const fileInput = document.querySelector('input[type="file"]');
    const file = makeFile();
    fireEvent.change(fileInput, { target: { files: [file] } });
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
    render(
      <UploadZone
        characterName="alice"
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    const fileInput = document.querySelector('input[type="file"]');
    fireEvent.change(fileInput, { target: { files: [makeFile()] } });
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith('Upload failed.');
    });
    expect(onUploaded).not.toHaveBeenCalled();
  });

  it('alerts on thrown error from apiSend', async () => {
    apiSend.mockRejectedValue(new Error('network down'));
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <UploadZone
        characterName="alice"
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    const fileInput = document.querySelector('input[type="file"]');
    fireEvent.change(fileInput, { target: { files: [makeFile()] } });
    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith('Upload error.');
    });
    errSpy.mockRestore();
  });

  it('toggles isDragging styles on dragenter and dragleave', () => {
    const { container } = render(
      <UploadZone
        characterName="alice"
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    const dropZone = container.querySelector('.aspect-video');
    fireEvent.dragOver(dropZone);
    expect(dropZone.className).toMatch(/border-emerald-500/);
    fireEvent.dragLeave(dropZone);
    expect(dropZone.className).not.toMatch(/border-emerald-500/);
  });

  it('alerts when dropped file fails voice validation', () => {
    render(
      <UploadZone
        characterName="alice"
        onCharacterNameChange={onCharacterNameChange}
        onUploaded={onUploaded}
      />
    );
    const dropZone = document.querySelector('.aspect-video');
    const badFile = new File(['x'], 'sample.txt', { type: 'text/plain' });
    Object.defineProperty(badFile, 'size', { value: 1024 });
    const dt = { files: [badFile] };
    fireEvent.drop(dropZone, { dataTransfer: dt });
    expect(window.alert).toHaveBeenCalled();
    expect(apiSend).not.toHaveBeenCalled();
  });
});
