'use client';

import * as React from 'react';
import { cn } from '@/lib/utils';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'destructive' | 'outline' | 'secondary' | 'ghost' | 'success';
  size?: 'default' | 'sm' | 'lg' | 'xl' | 'icon';
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'default', ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50',
        {
          'bg-primary text-primary-foreground hover:bg-primary/90': variant === 'default',
          'bg-destructive text-destructive-foreground hover:bg-destructive/90': variant === 'destructive',
          'border border-input bg-background hover:bg-accent': variant === 'outline',
          'bg-secondary text-secondary-foreground hover:bg-secondary/80': variant === 'secondary',
          'hover:bg-accent': variant === 'ghost',
          'bg-emerald-600 text-white hover:bg-emerald-700': variant === 'success',
        },
        {
          'h-10 px-4 py-2': size === 'default',
          'h-9 px-3': size === 'sm',
          'h-11 px-8 text-base': size === 'lg',
          'h-16 px-10 text-xl font-bold rounded-lg': size === 'xl',
          'h-10 w-10': size === 'icon',
        },
        className
      )}
      {...props}
    />
  )
);
Button.displayName = 'Button';

export { Button };
