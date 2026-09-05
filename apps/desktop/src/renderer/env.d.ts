import type { DesktopBridge } from "../shared/contracts";

declare global {
  interface Window {
    readonly workbench: DesktopBridge;
  }
}

export {};
