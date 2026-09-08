import type { TurnStreamEvent, TurnStreamRequest } from "../../shared/pdf";

export class TurnStreamFailure extends Error {
  readonly result: unknown;
  constructor(event: TurnStreamEvent) {super(event.text); this.result = event.result;}
}

export function streamTurn(request: TurnStreamRequest, update: (event: TurnStreamEvent) => void,
  signal?: AbortSignal): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const abort = () => { stop(); reject(new Error("Generation cancelled")); };
    const stop = () => { unsubscribe(); signal?.removeEventListener("abort", abort); };
    const unsubscribe = window.workbench.subscribeTurn(request, (event) => {
      update(event);
      if (event.event === "assistant.completed") { stop(); resolve(event.result); }
      if (event.event === "turn.failed") { stop(); reject(new TurnStreamFailure(event)); }
    });
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}
