/**
 * tests for components/ErrorBoundary.tsx
 *
 * Covers:
 * - renders children normally when no error occurs
 * - renders the fallback UI when a child throws during render
 * - shows the error message in the fallback
 * - shows the "Something went wrong" heading
 * - shows the "Reload page" button
 * - clicking "Reload page" calls window.location.reload
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ErrorBoundary } from '../components/ErrorBoundary';

// Component that throws unconditionally — used to trigger the boundary
function Bomb(): React.ReactNode {
  throw new Error('Test render error');
}

// Suppress the expected console.error noise that React emits when a component
// throws during render (both its own log and the jsdom re-throw via window
// error event). The spy is installed before every test and restored after.
let consoleErrorSpy: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  consoleErrorSpy.mockRestore();
});

describe('ErrorBoundary', () => {
  it('renders children when there is no error', () => {
    render(
      <ErrorBoundary>
        <div data-testid="child">hello</div>
      </ErrorBoundary>,
    );
    expect(screen.getByTestId('child')).toBeInTheDocument();
  });

  it('renders the fallback UI when a child throws', () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('displays the thrown error message in the fallback', () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByText('Test render error')).toBeInTheDocument();
  });

  it('shows the Reload page button', () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('button', { name: /reload page/i })).toBeInTheDocument();
  });

  it('calls window.location.reload when Reload page is clicked', async () => {
    const reload = vi.fn();
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { reload },
    });

    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );
    await userEvent.click(screen.getByRole('button', { name: /reload page/i }));
    expect(reload).toHaveBeenCalledOnce();
  });
});
