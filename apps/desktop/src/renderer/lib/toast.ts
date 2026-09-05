import { createElement } from "react";
import { toast } from "sonner";
import { AppToast, type ToastAction } from "../components/ui/app-toast";

type ErrorToastOptions = {
  action?: ToastAction;
  description: string;
  title: string;
};

export function showErrorToast({ action, description, title }: ErrorToastOptions): void {
  toast.custom(
    (id) => createElement(AppToast, { action, description, onDismiss: () => toast.dismiss(id), title }),
    { duration: Infinity, unstyled: true },
  );
}
