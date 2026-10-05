/** Keep the caller's deadline as well as the provider's normal request timeout. */
export function requestSignal(timeoutMs: number, signal?: AbortSignal): AbortSignal {
  signal?.throwIfAborted();
  const timeout = AbortSignal.timeout(timeoutMs);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}
