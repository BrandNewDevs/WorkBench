import type { HealthResponse } from "../../shared/contracts";

const healthMaxAgeMs = 30_000;

export function isHealthFresh(health: HealthResponse, now: number): boolean {
  const checkedAt = Date.parse(health.checkedAt);
  return Number.isFinite(checkedAt) && checkedAt <= now && now - checkedAt <= healthMaxAgeMs;
}
