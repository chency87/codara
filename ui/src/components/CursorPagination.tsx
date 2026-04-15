import React from 'react';

type CursorPaginationProps = {
  countLabel: string;
  pageLabel: string;
  canGoBack: boolean;
  canGoNext: boolean;
  onBack: () => void;
  onNext: () => void;
};

const buttonClass =
  'rounded-xl border px-3 py-2 text-[10px] font-black uppercase tracking-widest transition-colors disabled:cursor-not-allowed disabled:opacity-40';

const CursorPagination = ({ countLabel, pageLabel, canGoBack, canGoNext, onBack, onNext }: CursorPaginationProps) => (
  <div className="flex flex-col gap-3 border-t border-slate-800 px-6 py-4 text-slate-400 md:flex-row md:items-center md:justify-between">
    <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
      {countLabel}
      <span className="ml-3 text-slate-600">{pageLabel}</span>
    </div>
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={onBack}
        disabled={!canGoBack}
        className={`${buttonClass} border-slate-800 bg-slate-900/40 hover:border-slate-700 hover:text-white`}
      >
        Previous
      </button>
      <button
        type="button"
        onClick={onNext}
        disabled={!canGoNext}
        className={`${buttonClass} border-blue-500/20 bg-blue-500/10 text-blue-300 hover:border-blue-400/40 hover:text-blue-200`}
      >
        Next
      </button>
    </div>
  </div>
);

export default CursorPagination;
