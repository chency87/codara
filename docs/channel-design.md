# Channel Layer Guide

This document describes the current channel integration layer in Codara.

Current status:

- Telegram is fully implemented.
- Lark and Feishu are scaffolded as future adapters, not complete runtime integrations.

## 1. Channel Design Goals

The channel layer lets a user operate a Codara workspace from a messaging platform without inventing a second runtime model.

The key rule is:

- channel messages must be translated into the same user-bound execution flow already used by `/v1/chat/completions`

## 2. Runtime Architecture

```text
Telegram Update
   │
   ▼
TelegramChannelAdapter
   │
   ├─ validate bot / webhook secret / receive mode
   ├─ acknowledge inbound message
   └─ parse command or free-text turn
   │
   ▼
ChannelService
   │
   ├─ consume one-time link token
   ├─ resolve linked Codara user
   ├─ load or create channel conversation
   └─ update workspace/provider/session state
   │
   ▼
InferenceService
   │
   └─ execute the standard user-bound turn flow
   │
   ▼
TelegramChannelAdapter sends the reply
```

## 3. Persistence Model

Codara stores channel state in SQLite.

### 3.1 Linked external identities

`channel_user_links`

- keyed by `(channel, bot_name, external_user_id)`
- links one external identity to one Codara user

### 3.2 Conversation state

`channel_conversations`

- keyed by `(channel, bot_name, conversation_key)`
- stores:
  - `workspace_id`
  - `provider`
  - `session_label`
  - external chat and thread identifiers

### 3.3 Link tokens

`channel_link_tokens`

- one-time expiring link tokens
- created by operators from the management plane
- consumed by `/link <token>`

### 3.4 Runtime state

`channel_runtime_state`

- keyed by `(channel, bot_name, state_key)`
- currently used for persisted Telegram polling offsets

## 4. Telegram Configuration

Current config shape:

```toml
[channels.telegram]
enabled = true
receive_mode = "polling"   # or "webhook"
mention_only = false
api_base = "https://api.telegram.org"

[[channels.telegram.bots]]
name = "amesh-bot"
enabled = true
token = "..."
webhook_secret = "..."
username = "your_bot_username"
```

Important notes:

- multiple Telegram bots are supported
- bindings and conversations are keyed by both `channel` and `bot_name`
- only one receive mode is used at runtime for Telegram: `webhook` or `polling`

## 5. Telegram Receive Modes

### 5.1 Webhook mode

Route:

- `POST /channels/telegram/{bot_name}/webhook`

Expected verification header:

- `X-Telegram-Bot-Api-Secret-Token`

### 5.2 Polling mode

When `receive_mode = "polling"`:

- Codara starts a polling worker at application startup
- it calls `deleteWebhook` before polling
- it uses `getUpdates`
- it stores the latest offset in `channel_runtime_state`
- the webhook route returns `503` for that bot

## 6. Telegram Commands

Current built-in commands:

- `/start`
- `/help`
- `/commands`
- `/link <token>`
- `/whoami`
- `/workspace <workspace_id>`
- `/projects`
- `/project <name>`
- `/project_create <name> [default|python|docs|empty]`
- `/project_info <name>`
- `/provider <codex|gemini|opencode>`
- `/status`
- `/session`
- `/reset`

Codara also registers Telegram bot commands at startup via `setMyCommands`.

Project commands are scoped to the linked user's base workspace. For example,
`/project_create news-pulse python` creates `<user-workspace>/news-pulse`,
initializes the Python project layout, writes `.amesh/project.toml`, and
switches the conversation workspace to `news-pulse`.

## 7. Link Flow

```text
Operator creates user in dashboard or API
   │
   ▼
Operator creates link token for that user and bot
   │
   ▼
User opens Telegram bot chat
   │
   ▼
/link <raw_token>
   │
   ▼
channel_user_links row created
   │
   ▼
future messages resolve to that Codara user
```

Important boundary:

- the link token is not the user API key
- it is a one-time expiring channel-binding token

## 8. Conversation Flow

```text
User sends message
   │
   ▼
adapter resolves conversation key
   │
   ▼
ChannelService loads or creates conversation row
   │
   ▼
conversation row supplies:
  - workspace_id
  - provider
  - stable client_session_id
   │
   ▼
shared InferenceService executes turn
   │
   ▼
reply text returned to the chat
```

Conversation state is intentionally lightweight:

- it keeps workspace and provider selection
- it keeps stable session continuity
- it does not allow arbitrary filesystem paths from chat messages

## 9. Attachments and Acknowledgement

Current Telegram adapter behavior:

- text messages become ordinary user turns
- Telegram documents are staged into the bound workspace and forwarded through the shared attachment pipeline
- `/start`, `/help`, and `/commands` provide in-chat usage guidance
- `/whoami` reports the linked Codara user plus the current workspace/provider/session context
- inbound messages are acknowledged best-effort with:
  - `setMessageReaction`
  - fallback to `sendChatAction("typing")`

## 10. Security Model

Current channel security boundaries:

- external identities must be linked explicitly
- link tokens are one-time and expiring
- webhook requests can be secret-verified
- channel users are always routed through real Codara user bindings
- chat commands do not accept arbitrary `workspace_root` values

## 11. Current Scope and Future Work

Implemented today:

- Telegram multi-bot config
- webhook mode
- polling mode
- persisted polling offsets
- user linking
- workspace/provider/session commands
- text and document turns

Not fully implemented yet:

- Lark runtime adapter
- Feishu runtime adapter
- richer per-platform UI cards and structured interactive controls
