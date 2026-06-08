import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import VariantPanel from './VariantPanel.jsx';

describe('VariantPanel', () => {
  it('renders "Output A" label when id is "a"', () => {
    render(<VariantPanel id="a" images={['/a1.png']} onCommit={() => {}} />);
    expect(screen.getByText('Output A')).toBeInTheDocument();
  });

  it('renders "Output B" label when id is "b"', () => {
    render(<VariantPanel id="b" images={['/b1.png']} onCommit={() => {}} />);
    expect(screen.getByText('Output B')).toBeInTheDocument();
  });

  it('renders every image with the correct src and alt', () => {
    const images = ['/img1.png', '/img2.png', '/img3.png'];
    render(<VariantPanel id="a" images={images} onCommit={() => {}} />);
    for (let i = 0; i < images.length; i++) {
      const img = screen.getByAltText(`Variant A image ${i + 1}`);
      expect(img).toHaveAttribute('src', images[i]);
    }
  });

  it('calls onCommit with the id when the commit button is clicked', async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    render(<VariantPanel id="b" images={['/b1.png']} onCommit={onCommit} />);
    await user.click(screen.getByRole('button', { name: /Commit B/i }));
    expect(onCommit).toHaveBeenCalledWith('b');
  });

  it('falls back to index key when image src is empty', () => {
    render(<VariantPanel id="a" images={['', '/b.png']} onCommit={() => {}} />);
    expect(screen.getByText('No image')).toBeInTheDocument();
  });
});
