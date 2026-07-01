/**
 * Extract a human-readable error message from an Axios error or generic Error.
 *
 * Handles:
 * - 422 Pydantic validation errors (array of {loc, msg})
 * - Structured LLMError responses ({provider, reason, detail})
 * - Generic FastAPI {detail} / {error} responses
 * - Plain Error.message fallback
 *
 * @param {Error|import('axios').AxiosError} error
 * @returns {string} Readable error message
 */
export function extractErrorDetail(error) {
  const data = error?.response?.data;

  if (data) {
    // 422 Pydantic validation errors
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => `${(d.loc || []).join('.')}: ${d.msg}`).join('; ');
    }

    // Structured LLMError from backend (has provider field)
    if (data.provider) {
      const reason = data.reason || data.detail || 'Unknown error';
      return `[${data.provider}] ${reason}`;
    }

    // Generic detail string
    if (data.detail) return String(data.detail);

    // Some endpoints return {error: "..."}
    if (data.error) return String(data.error);
  }

  // Axios network errors (no response)
  if (error?.code === 'ECONNABORTED') return 'Request timed out';
  if (error?.code === 'ERR_NETWORK') return 'Network error';

  return error?.message || 'Unknown error';
}
