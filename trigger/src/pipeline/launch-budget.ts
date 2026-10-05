// Both launch tasks inherit the 600s limit in trigger.config.ts. Leave 60s for task teardown.
export const LAUNCH_RUN_BUDGET_MS = 540_000;
export const LAUNCH_WRITE_TIMEOUT_MS = 15_000;
export const LAUNCH_WRITE_BATCH_SIZE = 50;

export function hasTime(deadlineAt: number | undefined, durationMs: number): boolean {
  return deadlineAt === undefined || Date.now() + durationMs <= deadlineAt;
}

export function persistenceReserveMs(rows: number): number {
  return Math.max(1, Math.ceil(rows / LAUNCH_WRITE_BATCH_SIZE)) * LAUNCH_WRITE_TIMEOUT_MS + 5_000;
}
