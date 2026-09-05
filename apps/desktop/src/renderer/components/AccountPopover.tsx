import type { ReactNode } from "react";
import type { EmployeeSession } from "../../shared/contracts";
import { Popover, PopoverContent } from "./ui/popover";

type AccountAccess =
  | { kind: "authenticated"; session: EmployeeSession }
  | { kind: "developmentBypass" };

type AccountPopoverProps = {
  access: AccountAccess;
  children: ReactNode;
  onOpenChange: (open: boolean) => void;
  open: boolean;
};

type AccountRowProps = {
  title: string;
  value: string;
};

function AccountRow({ title, value }: AccountRowProps) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,10rem)] items-baseline gap-4 py-2.5 first:pt-0 last:pb-0">
      <dt className="text-xs text-muted-foreground">{title}</dt>
      <dd className="min-w-0 truncate text-right text-xs font-medium text-foreground" title={value}>
        {value}
      </dd>
    </div>
  );
}

function formatSessionExpiry(expiresAt: string): string {
  const date = new Date(expiresAt);
  return Number.isNaN(date.getTime())
    ? expiresAt
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function AccountDetails({ access }: { access: AccountAccess }) {
  if (access.kind === "developmentBypass") {
    return null;
  }

  return (
    <dl aria-label="Account details" className="mt-4 border-y border-border py-2.5">
      <AccountRow title="Employee" value={access.session.user.displayName} />
      <AccountRow title="Employee ID" value={access.session.user.employeeId} />
      <AccountRow title="Access" value="Authenticated employee" />
      <AccountRow title="Session expires" value={formatSessionExpiry(access.session.expiresAt)} />
    </dl>
  );
}

export function AccountPopover({ access, children, onOpenChange, open }: AccountPopoverProps) {
  const description = access.kind === "developmentBypass"
    ? "Authentication is disabled. This local build has no backend session or additional permissions."
    : "Current local employee and session details.";

  return (
    <Popover modal={false} onOpenChange={onOpenChange} open={open}>
      {children}
      <PopoverContent
        align="end"
        aria-describedby="account-popover-description"
        aria-labelledby="account-popover-title"
        collisionPadding={12}
        side="right"
        sideOffset={10}
      >
        <h2 className="text-sm font-medium text-foreground" id="account-popover-title">
          Account
        </h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground" id="account-popover-description">
          {description}
        </p>
        <AccountDetails access={access} />
      </PopoverContent>
    </Popover>
  );
}
