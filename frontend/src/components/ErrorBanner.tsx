interface ErrorBannerProps {
  message: string;
  /** Optional short, human-readable heading (e.g. "Backend unavailable"). */
  title?: string;
  onRetry?: () => void;
}

export function ErrorBanner({ message, title, onRetry }: ErrorBannerProps) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <div className="flex flex-col gap-0.5">
        {title ? <span className="font-semibold">{title}</span> : null}
        <span>{message}</span>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
        >
          Retry
        </button>
      ) : null}
    </div>
  );
}
