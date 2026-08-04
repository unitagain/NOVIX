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
      data-resize-handle={side}
      style={{ touchAction: 'none' }}
      className={`absolute inset-y-0 z-50 w-[14px] cursor-col-resize outline-none transition-colors before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-transparent hover:before:bg-blue-400 focus-visible:before:bg-blue-500 ${side === 'left' ? '-right-[7px]' : '-left-[7px]'}`}
    />
  );
}
