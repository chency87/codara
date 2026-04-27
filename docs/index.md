# Codara Documentation

Codara is a high-performance, stateful gateway designed to bridge OpenAI-compatible clients with CLI-native coding agents (such as Codex, Gemini, and OpenCode). It preserves session state, tracks workspace changes, and provides a robust operator control plane.

## Key Features

- **OpenAI Compatibility**: Drop-in replacement for OpenAI SDKs at `POST /v1/chat/completions`.
- **Session Persistence**: Reuses provider-side contexts across multiple turns via stable session IDs.
- **Workspace Isolation**: Manages filesystem-level isolation for different users and tasks.
- **Diff Tracking**: Automatically generates Git or hash-based diffs for every agent turn.
- **Operator Control Plane**: Rich management APIs and a React-based dashboard for monitoring and debugging.
- **Multi-Channel Support**: Native integration with communication channels like Telegram.

## System Sections

### [Architecture](./architecture.md)
Deep dive into the runtime components, data flow, and how the gateway orchestrates requests.

### [Core Concepts](./concepts.md)
Detailed explanation of Workspaces, Sessions, Tasks, and the Provider Adapter model.

### [API Reference](./management-api.md)
Documentation for the Inference API and the Management API.

### [Channels](./channel-design.md)
How Codara integrates with Telegram and other messaging platforms.

### [Observability](./observability.md)
Understanding traces, logs, and the system audit trail.

### [Deployment & Configuration](./deployment.md)
Guide for Docker-based deployment and `codara.toml` configuration.

---

## Quick Navigation

- [Installation & Quickstart](../README.md)
- [Project Layout](../SUMMARY.md)
- [Agent Workflow Guide](../AGENTS.md)
