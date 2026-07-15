import React from 'react';

export function PanelResizeHandle({ side, width, min, max, onResize }) {
  const handlePointerDown = (event) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const startX = event.clientX;
    const startWidth = width;

    const handlePointerMove = (moveEvent) => {
      const delta = moveEvent.clientX - startX;
      const next = side === 'left' ? startWidth + delta : startWidth - delta;
      onResize(Math.max(min, Math.min(max, next)));
    };
    const handlePointerUp = () => {
      document.removeEventListener('pointermove', handlePointerMove);
      document.removeEventListener('pointerup', handlePointerUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('pointermove', handlePointerMove);
    document.addEventListener('pointerup', handlePointerUp);
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onKeyDown={(event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const direction = event.key === 'ArrowRight' ? 1 : -1;
        onResize(Math.max(min, Math.min(max, width + direction * 12 * (side === 'left' ? 1 : -1))));
      }}
      className={`absolute inset-y-0 z-50 w-1 cursor-col-resize outline-none transition-colors hover:bg-blue-400/60 focus-visible:bg-blue-500 ${side === 'left' ? 'right-0' : 'left-0'}`}
    />
  );
}
