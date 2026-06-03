import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Sidebar from './Sidebar.jsx';

describe('Sidebar', () => {
  const noop = () => {};

  it('renders a brand mark', () => {
    render(<Sidebar activeTab="preview" onTabChange={noop} onToggleSettings={noop} settingsOpen={false} />);
    expect(screen.getByText('V')).toBeInTheDocument();
  });

  it('renders all three tab buttons with their labels', () => {
    render(<Sidebar activeTab="preview" onTabChange={noop} onToggleSettings={noop} settingsOpen={false} />);
    expect(screen.getByRole('button', { name: 'Director Canvas' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Voice Studio' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'A/B Testing' })).toBeInTheDocument();
  });

  it('renders the settings button', () => {
    render(<Sidebar activeTab="preview" onTabChange={noop} onToggleSettings={noop} settingsOpen={false} />);
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument();
  });

  it('marks the active tab with aria-pressed=true', () => {
    render(<Sidebar activeTab="voices" onTabChange={noop} onToggleSettings={noop} settingsOpen={false} />);
    expect(screen.getByRole('button', { name: 'Director Canvas' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Voice Studio' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'A/B Testing' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('marks the settings button as active when settingsOpen is true', () => {
    render(<Sidebar activeTab="preview" onTabChange={noop} onToggleSettings={noop} settingsOpen={true} />);
    expect(screen.getByRole('button', { name: 'Settings' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('calls onTabChange with the clicked tab id', async () => {
    const onTabChange = vi.fn();
    render(<Sidebar activeTab="preview" onTabChange={onTabChange} onToggleSettings={noop} settingsOpen={false} />);
    await userEvent.click(screen.getByRole('button', { name: 'A/B Testing' }));
    expect(onTabChange).toHaveBeenCalledWith('ab-testing');
  });

  it('calls onToggleSettings when the settings button is clicked', async () => {
    const onToggleSettings = vi.fn();
    render(<Sidebar activeTab="preview" onTabChange={noop} onToggleSettings={onToggleSettings} settingsOpen={false} />);
    await userEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(onToggleSettings).toHaveBeenCalledTimes(1);
  });
});
