// Types for repo.mjs, so the TypeScript suites in tests/plugin can import it.

export function normaliseRepo(url: string): string | null;
export function repoOf(cwd: string): Promise<string | null>;
