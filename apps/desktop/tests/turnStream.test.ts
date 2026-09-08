import assert from "node:assert/strict";
import test from "node:test";
import { streamTurn, TurnStreamFailure } from "../src/renderer/api/turnStream.ts";
import type { TurnStreamEvent } from "../src/shared/pdf.ts";

const request = {mode: "localConversation" as const, sessionId: "a4bde1b6-0e59-4919-a4dc-a3d660a5b264",
  clientRequestId: "baa0fa36-ae1c-44be-a571-ef63a18ea60b", message:"Hello"};

test("stream forwards ordered deltas and resets and resolves only on completion", async () => {
  let stopped = false;
  const events: TurnStreamEvent[] = [
    {event:"turn.accepted",text:"",result:null},
    {event:"assistant.delta",text:"old",result:null},
    {event:"assistant.reset",text:"",result:null},
    {event:"assistant.delta",text:"new",result:null},
    {event:"assistant.completed",text:"",result:{answer:"new"}},
  ];
  Object.defineProperty(globalThis, "window", {configurable:true, value:{workbench:{
    subscribeTurn: (_request: unknown, callback: (e:TurnStreamEvent)=>void) => {
      queueMicrotask(()=>events.forEach(callback));
      return ()=>{stopped=true;};
    },
  }}});
  let answer = "";
  const completed = await streamTurn(request, event => {
    if(event.event === "assistant.delta") answer += event.text;
    if(event.event === "assistant.reset") answer = "";
  });
  assert.equal(answer,"new"); assert.deepEqual(completed,{answer:"new"}); assert.equal(stopped,true);
});

test("cancel disconnects an incomplete turn", async () => {
  let stopped = false;
  Object.defineProperty(globalThis,"window",{configurable:true,value:{workbench:{
    subscribeTurn:()=>()=>{stopped=true;},
  }}});
  const controller = new AbortController();
  const pending = streamTurn(request,()=>{},controller.signal);
  controller.abort();
  await assert.rejects(pending,/cancelled/); assert.equal(stopped,true);
});

test("typed backend failure retains its diagnostic code", async () => {
  Object.defineProperty(globalThis,"window",{configurable:true,value:{workbench:{
    subscribeTurn:(_request:unknown, callback:(e:TurnStreamEvent)=>void)=>{
      queueMicrotask(()=>callback({event:"turn.failed",text:"Failed",result:{code:"invalid_ai_response"}}));
      return ()=>{};
    },
  }}});
  await assert.rejects(streamTurn(request,()=>{}),TurnStreamFailure);
});
