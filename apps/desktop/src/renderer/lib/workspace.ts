/** Employee workspaces deliberately separate ordinary chat from agentic workflows. */
export type WorkspaceView = "chat" | "qwenChat" | "settings";

/** A new employee session opens the ordinary local conversation surface. */
export const defaultEmployeeWorkspaceView: WorkspaceView = "qwenChat";
