import { cn } from "../../lib/utils";
import { Loader2 } from "lucide-react";

export function Button({ className, variant = "primary", size = "default", isLoading, children, ...props }) {
  const variants = {
    primary: "bg-primary text-black hover:bg-primary-hover border border-primary",
    secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80 border border-border",
    outline: "bg-transparent border border-primary/20 text-primary hover:bg-primary/10",
    ghost: "bg-transparent hover:bg-white/5 text-muted-foreground hover:text-white",
    destructive: "bg-destructive/10 text-destructive border border-destructive/20 hover:bg-destructive/20"
  };

  const sizes = {
    sm: "h-8 px-3 text-xs",
    default: "h-10 px-4 py-2",
    lg: "h-12 px-8 text-lg",
    icon: "h-10 w-10 p-0 flex items-center justify-center"
  };

  return (
    <button 
      className={cn(
        "inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className
      )}
      disabled={isLoading || props.disabled}
      {...props}
    >
      {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}
