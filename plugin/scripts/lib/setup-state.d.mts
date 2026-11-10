// Types for setup-state.mjs, so the TypeScript suites in tests/plugin can import it.

export const SYNC_FILE: string;
export const TEMPLATE_FILE: string;
export const HEADING: RegExp;
export function existing(path: string): { kind: 'none' } | { kind: 'other' } | { kind: 'file'; text: string };
export const onThisLaptop: (desk: string) => boolean;
export function notSetUp(root: string, desk: string): string[];
export function notSetUpNotice(missing: readonly string[]): string;
