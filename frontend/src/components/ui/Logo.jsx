import React from 'react';

export function Logo({ size = 'default', showText = true }) {
  const sizeClasses = {
    small: 'text-lg',
    default: 'text-2xl',
    large: 'text-4xl',
  };

  return (
    <div className="flex items-center gap-1">
      {/* Pixel-style icon */}
      {null}
      
      {showText && (
        <span 
          className={`inline-block pr-[0.10em] font-extrabold leading-none ${sizeClasses[size]} bg-gradient-to-r from-emerald-300 via-emerald-400 to-green-500 bg-clip-text text-transparent`}
          style={{ 
            fontFamily: '"Arial Rounded MT Bold", "Nunito", "Poppins", "Inter", system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
            letterSpacing: size === 'small' ? '-0.06em' : size === 'large' ? '-0.10em' : '-0.08em',
            textTransform: 'uppercase',
            textRendering: 'geometricPrecision'
          }}
        >
          NOVIX
        </span>
      )}
    </div>
  );
}

export function LogoFull({ className = '' }) {
  return (
    <div className={`flex flex-col items-center ${className}`}>
      <Logo size="large" />
      <span className="text-xs text-muted-foreground mt-1 tracking-widest">
        多智能体小说写作引擎
      </span>
    </div>
  );
}

export default Logo;
