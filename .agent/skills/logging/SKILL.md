---
name: logging
description: Guidelines for managing "Verbose" and "Temporary" logs to maintain context window health and user transparency.
---

# Logging Skill

This skill defines conventions for logging in the FreeCAD Nesting Workbench. The goal is to provide transparency to the user while preventing log spam from overwhelming the agent's context window.

## Log Categories

### 1. Verbose Logs (Persistent)
- **Purpose**: Give the user a deep understanding of the processes happening behind the scenes (e.g., specific placement metrics, trial evaluations, GA generation details).
- **Target**: FreeCAD Report View / Console.
- **Convention**:
  - MUST be gated by a `verbose` flag.
  - Useful for power users debugging their nesting results or understanding efficiency.
  - These logs stay in the console throughout the session.

### 2. Temporary Logs (Disposable)
- **Purpose**: Transient status updates meant for the agent or the user to monitor active progress.
- **Target**: UI Status Labels, Progress Bars, or temporary console messages.
- **Convention**:
  - SHOULD be overwritten or cleaned up when a task is completed.
  - In the agent context: When documenting a task's progress in `walkthrough.md`, only include significant milestones, not every per-part placement log.
  - Agent-facing logs (like those used for internal debugging during `EXECUTION`) should be removed before declaring a task finished.

## Best Practices

- **Avoid Redundancy**: Do not log the same information to multiple places unless they serve different immediate needs.
- **Context Awareness**: Use the `quiet` flag in algorithms when running "Batch" or "Optimization" loops to avoid flooding the console.
- **User Control**: Always provide a UI toggle for "Verbose Logging" if the process is long-running and generates significant output.
