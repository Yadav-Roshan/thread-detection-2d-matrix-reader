import * as React from 'react';
import { cn } from '@/lib/utils';

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'success' | 'destructive' | 'secondary';
}

function Badge({ className, variant = 'default', ...props }: BadgeProps) {
  return (
    <div
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold',
        {
          'border-transparent bg-primary text-primary-foreground': variant === 'default',
          'border-transparent bg-emerald-100 text-emerald-800': variant === 'success',
          'border-transparent bg-red-100 text-red-800': variant === 'destructive',
          'border-transparent bg-secondary text-secondary-foreground': variant === 'secondary',
        },
        className
      )}
      {...props}
    />
  );
}

export { Badge };
