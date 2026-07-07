# Design Philosophy of Detachable Shells

This is a purely human-written file on specs and design decisions made with the detachable shell tools in this repo. It explains why these tools were built and what they were built for.

---

I run a multiple coding agent sessions while ssh-ed on a shared remote machine, and I need these sessions to survive disconnects. Tmux is typically used to persist remote sessions, but it breaks the regular terminal experience. In particular:
- With `mouse off`, you can't scroll (but can copy)
- With `mouse on`, you can scroll, but tmux intercepts all your mouse commands so you can't drag, select and copy anymore.

An alternative, `shpool`, does not have the mouse select and scroll issue of tmux, but it by default doesn't allow fetch of earlier history and has fragile socket that dies after logout.

What I need, is essentially a *detachable shell*, something that is practically invisible over my existing terminal that just persists my sessions.

## Specs I needed

- **R1, Persistence**. A session (shell and running program) survives ssh disconnect, closing terminal window; and can be reattached later by name from different terminal window.
- **R2, Terminal-like interaction**. Typing, executing commands like it is any terminal session.
- **R3, Native mouse select/copy/paste**, like any regular GNOME or VSCode terminal these days.
- **R4, Proper Scrolling**. Mouse wheel can be used to scroll up and read sessions history, including output from before the session currently attached and output while detached.
- **R5, Multiple sessions**.

## Environment

Shared Linux remote machine, enter by SSH, either through (a) VSCode RemoteSSH or (b) SSH in GNOME terminal.
