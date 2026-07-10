import { EmptyState } from "../design";

export function ComingSoon({ what, phase }: { what: string; phase: string }) {
  return (
    <div className="mx-auto max-w-2xl pt-10">
      <EmptyState
        icon={
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
            <circle cx="14" cy="14" r="11" stroke="currentColor" strokeWidth="1.5" />
            <path d="M14 8v6l4 2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        }
        title={`${what} lands in ${phase}`}
        hint="This part of the app is scoped and on the roadmap — the navigation shows you where it will live."
      />
    </div>
  );
}
