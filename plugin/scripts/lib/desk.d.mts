// Types for desk.mjs, so the TypeScript suites in tests/plugin can import it.
// The plugin itself runs the .mjs with no build step.

export const DESK_TIMEOUT_MS: number;

export type DeskAnswer = { status: number; json: unknown };

export function deskRefusal(url: string): Promise<string | null>;

export function deskUrl(env?: Readonly<Record<string, string | undefined>>): string | null;

export function postForm(
  url: string,
  fields: Readonly<Record<string, string>>,
  options?: { timeoutMs?: number },
): Promise<DeskAnswer>;

export function postJson(
  url: string,
  body: unknown,
  options?: { token?: string; timeoutMs?: number },
): Promise<DeskAnswer>;

export function getJson(url: string, options?: { timeoutMs?: number }): Promise<DeskAnswer>;

// ALYDAS-391: plain http to this laptop (localhost, 127.0.0.1, [::1] or a
// name under localhost). False for anything that is not a URL.
export function isLocalDesk(url: string): boolean;

// For ALYDAS-392 (S3): one request to a desk on this laptop. Headers come
// back by lowercase name, a repeated one joined with ", ". A redirect is
// returned, not followed.
export type MachineAnswer = { status: number; headers: Record<string, string>; text: string };

export function requestThisMachine(
  url: string,
  options?: {
    method?: string;
    headers?: Readonly<Record<string, string>>;
    body?: string;
    timeoutMs?: number;
  },
): Promise<MachineAnswer>;

// ALYDAS-450: whether the desk still takes a token, asked of GET /api/mcp, and
// what its status code says.
export function probeConnection(desk: string, token: string, options?: { timeoutMs?: number }): Promise<DeskAnswer>;
export function connectionOf(answer: { status: number } | null | undefined): 'revoked' | 'live' | 'unknown';
