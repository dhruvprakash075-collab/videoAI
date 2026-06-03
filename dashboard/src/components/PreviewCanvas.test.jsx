import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PreviewCanvas from './PreviewCanvas.jsx';
import { createRef } from 'react';

describe('PreviewCanvas', () => {
  it('renders a video player when a video URL is provided', () => {
    const { container } = render(<PreviewCanvas video="/studio_outputs/final.mp4" scriptInputRef={createRef()} onScriptPicked={() => {}} />);
    const video = container.querySelector('video');
    expect(video).toBeTruthy();
    expect(video).toHaveAttribute('src', '/studio_outputs/final.mp4');
    expect(video).toHaveAttribute('controls');
  });

  it('renders the upload card when no video is provided', () => {
    render(<PreviewCanvas video={null} scriptInputRef={createRef()} onScriptPicked={() => {}} />);
    expect(screen.getByText('Upload Lore Script')).toBeInTheDocument();
    expect(screen.getByText('.txt format only')).toBeInTheDocument();
  });

  it('renders a file input that accepts only .txt', () => {
    render(<PreviewCanvas video={null} scriptInputRef={createRef()} onScriptPicked={() => {}} />);
    const input = document.querySelector('input[type="file"]');
    expect(input).toHaveAttribute('accept', '.txt');
  });

  it('calls onScriptPicked with the selected file', async () => {
    const onScriptPicked = vi.fn();
    render(<PreviewCanvas video={null} scriptInputRef={createRef()} onScriptPicked={onScriptPicked} />);
    const file = new File(['hello'], 'topic.txt', { type: 'text/plain' });
    const input = document.querySelector('input[type="file"]');
    await userEvent.upload(input, file);
    expect(onScriptPicked).toHaveBeenCalledTimes(1);
    expect(onScriptPicked.mock.calls[0][0]).toBe(file);
  });

  it('does not call onScriptPicked when no file is selected', async () => {
    const onScriptPicked = vi.fn();
    render(<PreviewCanvas video={null} scriptInputRef={createRef()} onScriptPicked={onScriptPicked} />);
    expect(onScriptPicked).not.toHaveBeenCalled();
  });
});
