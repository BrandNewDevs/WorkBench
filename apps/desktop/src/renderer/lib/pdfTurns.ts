export interface PendingPdfTurn {
  message: string;
  requestId: string;
}

export function pendingPdfTurn(
  previous: PendingPdfTurn | null,
  message: string,
  createRequestId: () => string,
): PendingPdfTurn {
  return previous?.message === message
    ? previous
    : {message, requestId: createRequestId()};
}
