# WorkBench

A monorepo managed with pnpm workspaces and Turborepo. The employee desktop client is an Electron, React, and TypeScript application. Other application boundaries can be filled in as their plans are approved.

## Structure

```text
.
├── apps/
│   ├── desktop/
│   ├── web/
│   ├── api/
│   └── ai/
├── packages/
│   └── .gitkeep
├── package.json
├── pnpm-workspace.yaml
├── pnpm-lock.yaml
└── turbo.json
```

- `apps/desktop` is the desktop application boundary.
- `apps/web` is the web application boundary.
- `apps/api` is the API boundary.
- `apps/ai` is the AI and Python integration boundary.

## Commands

```sh
pnpm install
pnpm dev
pnpm build
pnpm lint
pnpm typecheck
pnpm check
```

The root scripts run the matching task across workspaces through Turbo. `check` runs lint and typecheck.

## Packages

`packages/` is for reusable code shared by two or more apps. Examples include UI components, shared types, validation, and framework-neutral utilities. Keep app-specific code in its app.

Create a package when code has a clear shared owner and a second consumer, or when extracting it gives an app a clean boundary. Do not create a package for a single app's code just to fill the directory.

Use the `@workbench/` scope and a specific name, such as `@workbench/config` or `@workbench/ui`. A minimal package can start with:

```json
{
  "name": "@workbench/shared",
  "version": "0.0.0",
  "private": true
}
```

An app references a workspace package in its `package.json` like this:

```json
{
  "dependencies": {
    "@workbench/shared": "workspace:*"
  }
}
```

Keep dependencies moving in one direction. Apps may depend on shared packages. Shared packages must not depend on apps. Keep database and server-only code out of packages used by clients. Do not import database or server code into `apps/web` or `apps/desktop`, and do not have one app import another app. Use a shared package for code that truly belongs across those boundaries.

Python can later live in `apps/ai` beside its `pyproject.toml` and virtual environment. pnpm can still run workspace tasks while that app's scripts call Python tools or a small task runner.
