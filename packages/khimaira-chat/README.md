# khimaira-chat

Cross-session real-time chat MCP server for Claude Code. Daemon-push via `claude/channel` capability; sub-2s end-to-end latency.

Part of the [khimaira](https://github.com/fsocietydisobey/khimaira) workspace.

## What it does

- Lets two or more Claude Code sessions hold a real-time chat conversation.
- Messages land in the recipient's context as `<channel source="khimaira-chat" ...>` blocks via Claude Code's [channels](https://code.claude.com/docs/en/channels) feature.
- Groups + handshake — N-session rooms, all members must accept the invite before they receive messages.
- State (rooms, members, transcripts) lives in the `khimaira-monitor` daemon; this MCP server is a per-session stdio subprocess that subscribes to its session's events and forwards them to the agent.

## Architecture

See `tasks/khimaira-chat/IMPLEMENTATION.md` in the workspace root for the full design.

```
Claude Code session  ◄── stdio ──►  khimaira-chat MCP subprocess  ◄── HTTP/SSE ──►  khimaira-monitor daemon
```

## Install (per Claude Code peer)

`khimaira sync` registers this MCP server in your Claude Code config. To launch with channels enabled (research preview):

```bash
claude --dangerously-load-development-channels server:khimaira-chat
```

## Slash commands

- `/khimaira-chat <peers...>` — create + invite (or resume if same members already have a chat)
- `/khimaira-chat-accept <chat_id>` — accept an invite
- `/khimaira-chat-send <chat_id> <body>` — send a message
- `/khimaira-chat-history <chat_id>` — read transcript
- `/khimaira-chat-list` — your active chats
- `/khimaira-chat-leave <chat_id>` — leave a chat
- `/khimaira-chat-delete <chat_id>` — archive a chat (creator only)
- `/khimaira-chat-poll <chat_id>` — manual catch-up (escape hatch when channels misbehave)
