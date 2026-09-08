import { useEffect, useRef } from "react";
import { LoaderCircle, Send } from "lucide-react";
import { conversationMessageMaxLength } from "../../shared/contracts";
import type { QwenChat } from "../hooks/useQwenChat";
import { offlineChatStatusText, statusLine } from "../lib/qwenChat";
import { Message } from "./Message";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { Textarea } from "./ui/textarea";

type QwenChatPageProps = {
  chat: QwenChat;
  connected: boolean;
};

export function QwenChatPage({ chat, connected }: QwenChatPageProps) {
  const { state } = chat;
  const listRef = useRef<HTMLDivElement>(null);
  const messageCount = state.messages.length;
  const isSending = state.sendState === "sending";

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messageCount, isSending]);

  const canSend = connected && !isSending && !state.creatingSession && state.draft.trim().length > 0;
  const sendDisabledReason = !connected
    ? "Sending requires a local employee sign-in."
    : isSending
      ? "Waiting for the local model to finish."
      : state.creatingSession
        ? "Preparing the conversation."
        : state.draft.trim().length === 0
          ? "Write a message first."
          : undefined;

  const send = () => {
    if (canSend) chat.sendMessage();
  };

  return (
    <section className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)_auto] px-8 pb-16 pt-10">
      <header className="mx-auto w-full max-w-3xl">
        <h1 className="text-lg font-medium tracking-tight text-foreground">Local Qwen chat</h1>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">
          Plain multi-turn conversation with the local Qwen text model. Documents, tools, and workflow steps are not
          used in this mode.
        </p>
        <p className="mt-1 text-xs font-medium text-muted-foreground" role="status">
          {offlineChatStatusText}
        </p>
        {state.status && (
          <p className="mt-1 text-xs text-muted-foreground" role="status">
            {statusLine(state.status)}
          </p>
        )}
      </header>
      <div ref={listRef} className="min-h-0 overflow-y-auto">
        <div aria-label="Conversation messages" className="mx-auto flex w-full max-w-3xl flex-col gap-4 pb-4 pt-6">
          {state.messages.map((message) => (
            <Message key={message.messageId} message={message} />
          ))}
          {isSending && (
            <p className="text-sm text-muted-foreground" role="status">
              <LoaderCircle aria-hidden="true" className="mr-1.5 inline size-3.5 animate-spin align-[-2px]" strokeWidth={1.75} />
              Waiting for the local model…
            </p>
          )}
          {state.messagesState === "loading" && (
            <p className="text-sm text-muted-foreground" role="status">Loading messages…</p>
          )}
          {state.messagesState === "error" && (
            <div className="rounded-lg border border-border bg-muted/30 px-4 py-3" role="status">
              <p className="text-sm text-foreground">Persisted messages could not be loaded from FastAPI.</p>
              <Button className="mt-2 h-8 px-2.5 text-xs" onClick={chat.retryMessages} type="button" variant="outline">
                Retry
              </Button>
            </div>
          )}
          {!isSending && state.messagesState === "ready" && messageCount === 0 && (
            <p className="text-sm text-muted-foreground">Ask a question to check the local model. Replies stay on this workstation.</p>
          )}
        </div>
      </div>
      <div className="mx-auto w-full max-w-2xl pt-4">
        <div className="relative overflow-hidden rounded-lg border border-border bg-background shadow-sm">
          <Label className="sr-only" htmlFor="qwen-chat-draft">Message draft</Label>
          <Textarea
            className="field-sizing-content min-h-16 max-h-48 resize-none overflow-y-auto rounded-none border-0 bg-transparent px-3 py-2.5 pb-12 pr-14 shadow-none focus-visible:border-transparent focus-visible:ring-0"
            id="qwen-chat-draft"
            maxLength={conversationMessageMaxLength}
            onChange={(event) => chat.setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                send();
              }
            }}
            placeholder="Message the local model"
            value={state.draft}
          />
          <div className="absolute bottom-1.5 right-2">
            <Button
              aria-busy={isSending}
              aria-label={isSending ? "Waiting for the local model" : sendDisabledReason ?? "Send message"}
              disabled={!canSend}
              onClick={send}
              size="icon"
              title={sendDisabledReason}
              type="button"
              variant="ghost"
            >
              {isSending ? (
                <LoaderCircle aria-hidden="true" className="size-4 animate-spin" strokeWidth={1.75} />
              ) : (
                <Send aria-hidden="true" className="size-4" strokeWidth={1.75} />
              )}
            </Button>
          </div>
        </div>
        {state.sendError !== undefined && (
          <p aria-live="assertive" className="mt-2 text-sm text-destructive" role="status">{state.sendError}</p>
        )}
      </div>
    </section>
  );
}
