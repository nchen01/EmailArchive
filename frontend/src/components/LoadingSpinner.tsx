interface LoadingSpinnerProps {
  label?: string;
}

export function LoadingSpinner({ label }: LoadingSpinnerProps) {
  return (
    <div className="flex items-center gap-3 text-muted">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-line2 border-t-slate-600" />
      {label ? <span className="text-sm">{label}</span> : null}
    </div>
  );
}
