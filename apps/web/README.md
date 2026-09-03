# WorkBench web

`apps/web` is the WorkBench single-page application. Vite runs the development server and production build. The UI uses React with TypeScript and TSX, Chakra UI, and Emotion. ESLint checks the source, pnpm manages dependencies, and Turborepo runs workspace tasks from the repository root.

TSX is TypeScript with JSX syntax.

## Install

Run this from the repository root:

```sh
pnpm install
```

## Commands from the repository root

The workspace filter targets `@workbench/web`:

```sh
pnpm --filter @workbench/web dev
pnpm --filter @workbench/web build
pnpm --filter @workbench/web preview
pnpm --filter @workbench/web lint
pnpm --filter @workbench/web typecheck
```

`preview` serves the already-built `dist` directory. Run the build first. Stop it with `Ctrl-C` when testing locally.

## Commands from `apps/web`

```sh
pnpm dev
pnpm build
pnpm preview
pnpm lint
pnpm typecheck
```

The app scripts map directly to Vite, ESLint, and TypeScript. `build` runs `vite build`, while `preview` runs `vite preview`.
