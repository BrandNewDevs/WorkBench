import * as React from "react";
import { Toaster as Sonner, type ToasterProps } from "sonner";
import "sonner/dist/styles.css";
import { cn } from "@/lib/utils";

function Toaster({ className, toastOptions, offset, ...props }: ToasterProps) {
  return (
    <Sonner
      {...props}
      className={cn("workbench-toaster", className)}
      closeButton={props.closeButton ?? true}
      offset={offset ?? { top: "calc(var(--workbench-titlebar-height) + 12px)", right: 16 }}
      position={props.position ?? "top-right"}
      richColors={props.richColors ?? false}
      swipeDirections={props.swipeDirections ?? []}
      theme={props.theme ?? "light"}
      toastOptions={{
        closeButton: true,
        closeButtonAriaLabel: "Dismiss notification",
        ...toastOptions,
      }}
    />
  );
}

export { Toaster };
