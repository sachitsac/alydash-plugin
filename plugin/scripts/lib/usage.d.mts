// Types for usage.mjs, so the TypeScript suites in tests/plugin can import it.

// A session's count as its state file keeps it: every token, the dollars of
// the messages that could be priced, how many those were, and how far each
// transcript has been read ('' is the session's own, the rest its subagents').
export type Usage = {
  tokens: number;
  costUsd: number;
  priced: number;
  files: Record<string, { at: number; ids: string[] }>;
};

export const PRICES: Readonly<Record<string, readonly number[]>>;
export const READ_MAX_BYTES: number;
export const SUBAGENTS_MAX: number;
export const TOKENS_MAX: number;
export const COST_MAX: number;

export function baseModel(model: string | null | undefined): string;
export function priceMessage(model: string | null | undefined, usage: unknown): number | null;
export function tokensOf(usage: unknown): number;
export function restartUsage(usage: Usage | null | undefined): Usage;
export function readUsage(transcriptPath: unknown, previous?: Usage | null): Usage;
export function usageFigures(usage: Usage | null | undefined): { tokens: number; costUsd: number | null } | null;
