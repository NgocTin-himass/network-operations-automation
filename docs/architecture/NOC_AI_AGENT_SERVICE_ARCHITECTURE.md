# NOC AI Agent Service Architecture

- Status: Approved architecture baseline
- Date: 2026-11-24
- Repository baseline: main at commit bb79b4f0eed6ef9c18684d34375527b0122398fa
- Scope: Independent Agent Service, Discord as the operator interface, automatic and manual investigation, read-only MVP

## 1. Approved architecture decisions

| Decision | Selected approach |
|---|---|
| Agent deployment | Independent NOC AI Agent Service, separate process and configuration from Network Automation Backend |
| Operator interface | Discord Bot; no React, Vue, web frontend, or mobile app required for MVP |
| Investigation triggers | Automatic when Backend publishes a qualifying Incident event; manual when an authorized engineer requests investigation through Discord |
| Initial permissions | Read-only tools only; privileged remediation and approval workflow are deferred |
| AI provider | OpenAI Responses API with function calling |
| Data ownership | Network Automation Backend remains the source of truth for devices, credentials, monitoring metrics, alerts, incidents, and operational history |
| Service boundary | Agent Service calls Backend APIs; it never connects directly to the Backend SQLite database or devices |
| Bot deployment | Discord Bot runs as a component inside the independent Agent Service; it is not a fourth service in MVP |

The Agent Service is independent from the Backend, but it is not an independent infrastructure administrator. It coordinates the model and requests approved read-only capabilities from the Backend.

## 2. Repository audit: current baseline

The repository was inspected before defining this architecture. The following statements describe the current main branch at the baseline commit above.

### 2.1 Existing components

- **main.py** orchestrates the monitoring run: Ping, REST API, SSH, Cisco fixture parsing, normalization, health checks, state tracking, reports, and optional Discord webhook notifications.
- **collectors/** contains Ping, REST API, and SSH collectors.
- **api/mock_nms_api.py** is a Mock NMS service. It exposes health and mock device endpoints for the existing REST API Collector. It is not the Backend API that the AI Agent will use.
- **storage/database.py** creates the existing SQLite monitoring schema.
- **storage/state_tracker.py** maintains current device state, state history, state events, active alerts, alert events, and metric history.
- **alerts/discord_notifier.py** sends outbound notifications through a Discord Incoming Webhook. It does not receive commands or provide conversational interactions.
- **config/devices.yaml** is the current device inventory configuration.
- **.github/workflows/ci.yml** runs the existing Pytest suite.

### 2.2 Existing database tables

The current SQLite schema contains:

1. monitoring_runs
2. device_current_state
3. device_state_history
4. state_events
5. active_alerts
6. alert_events
7. device_metrics_history

The current schema does not yet contain an Incident entity, Incident event history, an Outbox, Agent jobs, or Agent conversations.

### 2.3 Important implementation distinctions

- The current FastAPI application is the Mock NMS API, not a general-purpose tool API for the Agent.
- The existing Discord Incoming Webhook is outbound-only. A Discord Bot is required for engineers to interact with the Agent.
- The existing Alert Manager and State Tracker deduplicate active alert events, but the inspected source does not implement a complete Incident lifecycle or a 3-transitions-in-15-minutes flapping suppression policy.
- The current SSH collection loop in main.py processes configured Linux SSH devices sequentially. Do not describe SSH collection as parallel unless the implementation is changed and tested.
- The existing Cisco CLI data is fixture-based and simulated. The Agent must not report it as live Cisco telemetry.
- The current project has no OpenAI SDK integration, no Discord Bot dependency, and no independent Agent Service.

The existing monitoring pipeline must remain functional while the new capabilities are added.

## 3. Target logical architecture

~~~mermaid
flowchart TB
    subgraph BACKEND["Network Automation Backend"]
        MON["Existing Monitoring Pipeline<br/>Cron → Collectors → Health Check → State Tracker"]
        INC["Incident Manager"]
        OUT["Transactional Outbox"]
        API["Backend Tool API<br/>Inventory · Metrics · Alerts · Logs · History"]
        DB[("Backend SQLite<br/>Source of Truth")]
        MON --> DB
        MON --> INC
        INC --> DB
        INC --> OUT
        API --> DB
    end

    subgraph AGENT["Independent NOC AI Agent Service"]
        CONSUMER["Incident Event Consumer"]
        BOT["Discord Bot Adapter"]
        JOB["Agent Job Manager / Worker"]
        ORCH["Agent Orchestrator"]
        TOOLS["Backend API Client / Tool Router"]
        CONV[("Agent State Store<br/>Jobs · Conversations · Discord mapping")]
        CONSUMER --> JOB
        BOT --> JOB
        JOB --> ORCH
        ORCH --> TOOLS
        JOB --> CONV
        BOT --> CONV
    end

    LLM["OpenAI Responses API"]
    DISCORD["Discord Server / NOC Engineers"]

    OUT -->|"Authenticated incident event delivery"| CONSUMER
    TOOLS -->|"Authenticated read-only API requests"| API
    ORCH <-->|"Responses API / Function Calling"| LLM
    BOT <-->|"Discord Gateway / Application Commands"| DISCORD
    BOT -->|"Investigation reports and progress"| DISCORD
~~~

### 3.1 Network Automation Backend

The Backend owns infrastructure access and authoritative operational data.

Responsibilities:

- Run the existing monitoring pipeline.
- Manage device inventory and allowed device scope.
- Hold SSH/API credentials and connect to infrastructure.
- Expose narrowly scoped read-only APIs to the Agent.
- Maintain metrics, alerts, incidents, and incident history.
- Create and deliver Incident events through a transactional Outbox.
- Authenticate and authorize the Agent Service.
- Record API access and tool execution audit events.
- Continue sending the existing monitoring alerts through the current notification path until a deliberate migration is tested.

The Backend is the only component allowed to use existing Collectors or access the operational SQLite database.

### 3.2 Independent NOC AI Agent Service

The Agent Service is a separate Python application and runtime.

Responsibilities:

- Host the Discord Bot.
- Receive automatic Incident events from the Backend.
- Receive authorized manual investigation requests from Discord.
- Create and manage Agent jobs.
- Call the OpenAI Responses API.
- Register and orchestrate function tools.
- Call Backend read-only APIs using a service identity.
- Maintain conversation-to-Incident and conversation-to-Discord mappings.
- Format and deliver investigation results to Discord.
- Apply limits for model turns, tool calls, retries, timeouts, and concurrent jobs.

The Agent Service must not:

- Open the Backend SQLite database file.
- Connect directly to Server, Router, or Switch SSH endpoints.
- Read or store Backend SSH private keys.
- Receive Database passwords or infrastructure credentials.
- Execute arbitrary shell commands.
- make privileged infrastructure changes in the MVP.

### 3.3 OpenAI Model

The OpenAI model is the reasoning and tool-selection component.

It may:

- Interpret Incident context or an engineer's question.
- Select from the read-only function tools provided by the Agent Service.
- Analyze returned evidence.
- Distinguish observed facts, hypotheses, and confirmed root causes.
- Produce a structured investigation report.

It does not execute infrastructure operations. Function calling is a request from the model; the Agent Service validates and executes the corresponding application-side function, then returns the result to the model.

### 3.4 Discord Bot

The Discord Bot is the operator interface and runs inside the Agent Service.

MVP interaction model:

- Use Discord Gateway and application/slash commands.
- Do not require a separate web frontend or a publicly exposed interaction endpoint.
- Respond promptly to a command, create or reuse an Incident thread, and run long investigation work asynchronously.
- Persist Discord channel, thread, user, Incident, and Agent conversation mappings.
- Authorize by configured Guild ID, Channel ID, Discord User ID, and allowed Role IDs.
- Do not trust a display name as an identity.
- Use slash commands for the first version. Free-form message listening can be considered later and must be explicitly configured with the required Discord message-content intent.

The existing Incoming Webhook and the new Bot have different jobs:

- Existing webhook: sends monitoring alerts.
- Bot: receives engineer commands and sends AI investigation responses.

They can post to the same NOC channel during MVP. Do not place the Bot token inside the existing webhook notifier.

## 4. Two investigation entry points

Both entry points use the same Agent Orchestrator, tool definitions, Backend API Client, and investigation instructions.

### 4.1 Automatic Incident Investigation

1. Monitoring runs and detects a qualifying alert transition.
2. Backend persists the alert/Incident update and an Outbox event in the same database transaction.
3. Outbox dispatcher delivers the event to the Agent Service using authenticated service-to-service communication.
4. Agent Service validates the event, checks its event ID for duplicates, and creates an Agent job.
5. Worker starts an investigation using the Incident ID and limited Incident context.
6. GPT selects required read-only tools.
7. Agent Service calls Backend APIs; Backend executes queries or existing collectors within approved scope.
8. Tool results are returned to GPT for analysis.
9. Agent produces an evidence-based report.
10. Agent Service posts the report to the appropriate Discord Incident thread.
11. Agent job status and report reference are persisted in Agent State Store. Backend remains authoritative for Incident data.

Automatic trigger policy:

- A newly raised Incident can trigger an investigation.
- A material severity escalation can trigger a follow-up investigation according to a defined policy.
- Repeated samples for an unchanged active alert must not create duplicate Agent jobs.
- A resolved Incident should generate a recovery/update notification in its existing thread; it should not automatically start a new root-cause investigation.
- Manual requests may re-run or deepen an investigation, subject to rate limits and authorization.

### 4.2 Manual Investigation through Discord

Proposed MVP commands:

| Command | Purpose |
|---|---|
| /noc investigate incident_id | Investigate a current or historical Incident by exact ID |
| /noc inspect device_id | Investigate the latest known state of a registered device |
| /noc history device_id period | Analyze stored history for a device and time range |
| /noc compare incident_id | Compare the stored Incident evidence with current device state |
| /noc ask incident_id question | Continue a conversation in the context of an Incident |

An Incident ID lookup must work for both open and resolved Incidents.

A historical investigation must use timestamps and stored historical evidence. It must not present current metrics as if they were values observed during the old Incident.

A current-state investigation must retrieve fresh data from the Backend, with collection timestamps. It must not rely solely on a historical snapshot.

### 4.3 Conversation continuity

- Each Incident can have a Discord thread.
- The Agent State Store maps Discord guild/channel/thread/user identifiers to a conversation ID and optional Incident ID.
- Follow-up questions in the same Incident context reuse the mapped conversation.
- The Agent must disclose when context is missing or expired and fetch authoritative Incident information again.
- Do not rely exclusively on model conversation memory for operational facts; retrieve current or historical facts from Backend Tools.

## 5. Incident lifecycle and event model

The current project has alert tables but no first-class Incident entity. Add an Incident Manager without replacing the existing Alert Manager.

MVP correlation rule:

- One Incident represents one alert episode identified by the existing alert key (device + check).
- A new RAISED event creates a new Incident if no active Incident exists for that alert key.
- An UPDATED event updates the matching active Incident and appends an Incident event.
- A RESOLVED event resolves the matching Incident.
- If the same alert is raised again after resolution, it creates a new Incident ID.

This one-alert-episode rule is intentionally simple for MVP. Correlating multiple alerts into a single multi-device root-cause Incident is a later enhancement.

Suggested Backend tables:

### incidents

- incident_id: stable unique ID.
- alert_key: alert correlation key from the current Alert Manager.
- device_id: affected device.
- check_name: metric or check that caused the Incident.
- severity: current Incident severity.
- status: OPEN, INVESTIGATING, RESOLVED, CLOSED.
- title and summary.
- first_seen_at and last_seen_at.
- resolved_at.
- created_at and updated_at.

Use a uniqueness rule that prevents more than one active Incident for the same alert key while permitting a new Incident after the previous one is resolved.

### incident_events

- event_id.
- incident_id.
- source_alert_event_id.
- event_type: CREATED, UPDATED, SEVERITY_ESCALATED, RESOLVED.
- previous_severity and current_severity where applicable.
- event payload and timestamp.

### agent_outbox

- event_id: unique event identifier.
- event_type: incident.created, incident.updated, incident.resolved.
- aggregate_id: Incident ID.
- schema_version.
- payload_json.
- delivery_status: PENDING, IN_FLIGHT, DELIVERED, DEAD_LETTER.
- attempt_count.
- next_attempt_at.
- created_at, delivered_at, last_error.

Incident updates and their Outbox event must be committed atomically in the Backend database. A dispatcher delivers pending events with retry and backoff. The Agent Service deduplicates delivery by event_id because Outbox delivery is at-least-once, not exactly-once.

The existing seven monitoring tables remain in place. Add new tables through non-destructive migrations.

## 6. Agent Service data model

The Agent Service owns only its own operational state. It must use a separate Agent database or datastore, not the Backend SQLite file.

Suggested MVP tables:

### agent_jobs

- job_id.
- source: AUTOMATIC_EVENT or DISCORD_MANUAL.
- source_event_id: unique for automatic events.
- incident_id and device_id.
- requested_by_discord_user_id where applicable.
- status: PENDING, RUNNING, SUCCEEDED, FAILED, CANCELLED.
- attempt_count.
- created_at, started_at, finished_at.
- error_code and sanitized error_message.

### agent_conversations

- conversation_id.
- incident_id (nullable for general device conversations).
- discord_guild_id.
- discord_channel_id.
- discord_thread_id.
- discord_user_id.
- created_at and updated_at.
- status.

### agent_job_events

- job_event_id.
- job_id.
- event_type.
- sanitized detail.
- created_at.

Do not store infrastructure secrets or raw credentials in Agent State Store. Avoid persisting complete sensitive logs or unrestricted tool outputs. Store references and bounded, sanitized summaries where possible.

Incident status and Agent Job status are separate concepts. An Agent job can be SUCCEEDED while the Incident remains OPEN because diagnosis does not mean remediation occurred.

## 7. Backend API contract

The existing Mock NMS API must remain separate. Create a new Backend API surface for the Agent; do not repurpose the mock NMS endpoints.

Initial read-only API groups:

| API capability | Example endpoint | Purpose |
|---|---|---|
| Device inventory | GET /api/v1/devices | List authorized registered devices |
| Device details | GET /api/v1/devices/{device_id} | Resolve device identity and type |
| Incident details | GET /api/v1/incidents/{incident_id} | Read current or historical Incident |
| Incident history | GET /api/v1/incidents/{incident_id}/events | Read Incident lifecycle |
| Current metrics | GET /api/v1/devices/{device_id}/metrics/latest | Retrieve latest metrics with timestamps |
| Metric history | GET /api/v1/devices/{device_id}/metrics | Query metrics within a bounded time range |
| Alerts | GET /api/v1/alerts | Query active or recent alerts |
| Device history | GET /api/v1/devices/{device_id}/history | Read state history |
| Network interfaces | GET /api/v1/devices/{device_id}/interfaces | Read collected interface data |
| Service status | GET /api/v1/devices/{device_id}/services/{service_name} | Read service state where supported |
| Recent logs | GET /api/v1/devices/{device_id}/logs | Read bounded, sanitized logs where supported |
| Ping check | POST /api/v1/devices/{device_id}/diagnostics/ping | Run a bounded, allow-listed reachability check |

All endpoints must validate device scope and query limits. Logs must have bounded line counts, bounded time ranges, and sensitive-value redaction.

The first implementation may use existing YAML configuration as the inventory source if that is the safest incremental path. A database-backed Device Inventory can be introduced later without changing Agent tool names. Device credential management remains Backend-owned.

### Service-to-service authentication

For MVP, use a dedicated Agent service identity and short-lived machine access token:

- Agent authenticates to Backend with its service credential.
- Backend issues a short-lived JWT with a fixed issuer, audience, subject, expiry, and explicit read-only scopes.
- Backend validates signature, issuer, audience, expiry, and scope on every request.
- Rotate the Agent service credential.
- Keep transport private and use HTTPS when services communicate across hosts.
- Never send the token to OpenAI or Discord.
- Do not create a user refresh-token flow for the service account; use machine credential authentication to obtain a new short-lived access token.

The Backend must enforce authorization independently of model output and Agent prompts.

## 8. OpenAI function calling contract

The Agent Service owns tool schemas and the function-calling loop.

Initial tool set:

- list_devices
- get_device
- get_incident
- get_incident_events
- get_current_metrics
- get_metrics_history
- get_recent_alerts
- get_device_history
- get_network_interfaces
- get_service_status
- get_recent_logs
- ping_device

Tool definitions should use strict JSON schemas where supported, with required fields, enum constraints, and additionalProperties disabled.

Execution sequence:

1. Agent Service builds a bounded context from the Incident or Discord request.
2. It sends instructions, input, and approved function tools to OpenAI Responses API.
3. The model returns text or one/multiple function calls.
4. Agent Service validates the function name and arguments against its registry.
5. Agent Service calls the corresponding Backend API.
6. Agent Service sanitizes and bounds the tool result.
7. Agent Service returns the tool result to the model.
8. Repeat until the model returns a final response or a configured limit is reached.
9. Validate the final report schema and deliver it to Backend/Discord.

Guardrails:

- Maximum model turns per job.
- Maximum tool calls per job.
- Per-tool timeout and overall job deadline.
- Bounded log and metrics payload sizes.
- Retry only transient failures and only within a fixed limit.
- Tool failures must be returned as structured errors, not fabricated values.
- The model cannot select arbitrary URLs, IPs, shell commands, SQL statements, or file paths.
- Logs, device descriptions, and API response strings are untrusted data, never instructions.

## 9. Discord interaction and authorization

Initial commands are slash/application commands handled through the Discord Gateway. This avoids requiring a public inbound web server for Discord interactions in a local VMware lab.

MVP commands:

- /noc investigate incident_id
- /noc inspect device_id
- /noc history device_id period
- /noc compare incident_id
- /noc ask incident_id question

Long-running commands must acknowledge promptly and run in a background Agent job. Post progress and final output into the Incident thread. Do not hold an interaction handler open while waiting for a long investigation.

Authorization must verify:

- Expected Discord Guild ID.
- Allowed Channel ID or channel category.
- Discord User ID and configured Role IDs.
- Command-specific permissions.
- Backend scope for every tool operation.

Use Discord User ID and Role IDs, not display names, as authorization inputs. The Backend remains the final authority for device and data access.

The Agent Bot token and OpenAI API key exist only in the Agent Service environment. The existing monitoring webhook URL remains separate.

## 10. Security boundaries

### Backend owns

- SSH private keys and passwords.
- API credentials for monitored devices.
- Device inventory and allowed device scope.
- Database access.
- Collector execution.
- Backend API authorization.
- Audit records for tool requests and results.

### Agent Service owns

- OpenAI API key.
- Discord Bot token.
- Backend machine identity credential.
- Agent job state.
- Conversation and Discord thread mapping.
- Model instructions and tool schemas.

### OpenAI receives

- Minimum Incident context needed for analysis.
- Sanitized, bounded metrics and logs.
- Tool outputs required for diagnosis.

OpenAI must never receive:

- SSH private keys or passwords.
- Backend machine credentials or JWTs.
- Discord Bot token.
- Database credentials.
- Unredacted secrets found in logs.

### Read-only MVP

No restart, stop, configuration change, route change, shell execution, or privileged command tool is registered.

Approval and remediation are a later phase. Adding a prompt instruction is not a substitute for Backend authorization.

## 11. Deployment topology

For the Ubuntu VMware lab, run two independent applications:

1. Existing Network Automation Backend/Monitoring environment.
2. New NOC AI Agent Service environment.

Each has its own configuration and process lifecycle. The Agent Service can be run under its own systemd unit or separate process. The Discord Bot uses an outbound Gateway connection; no public web frontend is required.

For local development:

- Backend uses its existing virtual environment and existing dependency set.
- Agent Service uses its own virtual environment and dependency set.
- Agent Service has its own environment file.
- Backend and Agent communicate over a private local network or loopback in the same VM.
- Do not add OpenAI or Discord Bot dependencies to the existing monitoring requirements unless a specific Backend component needs them.

Suggested Agent Service environment variables:

- OPENAI_API_KEY
- OPENAI_MODEL
- DISCORD_BOT_TOKEN
- DISCORD_GUILD_ID
- DISCORD_ALLOWED_CHANNEL_IDS
- DISCORD_ALLOWED_ROLE_IDS
- BACKEND_BASE_URL
- BACKEND_CLIENT_ID
- BACKEND_CLIENT_SECRET
- AGENT_DATABASE_URL or AGENT_STATE_DB_PATH
- AGENT_MAX_CONCURRENT_JOBS
- AGENT_MAX_TOOL_CALLS
- AGENT_JOB_TIMEOUT_SECONDS

Do not commit real values. Add placeholders only to an example file.

## 12. Implementation phases

### Phase 0 – Architecture baseline
- Record decisions and repository audit.
- Define ownership and service boundaries.
- Define event and API contracts.

### Phase 1 – Backend Incident and Outbox
- Add Incident Manager and non-destructive SQLite migrations.
- Convert alert RAISED/UPDATED/RESOLVED events into Incident lifecycle events.
- Add transactional Outbox.
- Add Outbox dispatcher and delivery retry.
- Keep existing monitoring and alert tests passing.

### Phase 2 – Backend Read-only API
- Add a new Backend API separate from Mock NMS.
- Expose inventory, Incident, metrics, alert, state history, and bounded diagnostics endpoints.
- Add service authentication, scope checks, redaction, and audit logs.
- Test endpoints independently.

### Phase 3 – Independent Agent Service foundation
- Create separate Python application and virtual environment.
- Add health/readiness endpoint, settings, logging, job persistence, and Backend API client.
- Receive and deduplicate Incident events.
- No model or Discord integration until this boundary is tested.

### Phase 4 – OpenAI Agent Orchestrator
- Add OpenAI Responses API client.
- Add strict read-only tool registry and execution loop.
- Add turn, tool, timeout, and payload limits.
- Test model tool calls using mocked OpenAI responses and mocked Backend API responses.

### Phase 5 – Discord Bot
- Add slash commands and authorization.
- Add conversation and Incident-thread mapping.
- Add asynchronous job acknowledgements, progress, and final reports.
- Test historical and current Incident lookups.

### Phase 6 – Automatic investigation
- Connect Backend Outbox delivery to Agent Service.
- Test incident.created, severity escalation, duplicate delivery, recovery, Agent downtime, retries, and replay.
- Keep the existing alert webhook path working.

### Phase 7 – Hardening
- Review logs and secret redaction.
- Test rate limiting and job concurrency.
- Test service restarts and recovery.
- Confirm the monitoring pipeline is unchanged.
- Approval/remediation is explicitly out of scope until a separate design and test phase.

## 13. MVP acceptance criteria

The MVP is complete when:

1. Backend and Agent Service start and stop independently.
2. Agent Service cannot access the Backend SQLite file or SSH credentials.
3. Backend can deliver a new Incident event to Agent Service.
4. Duplicate event delivery does not create duplicate automatic Agent jobs.
5. A new qualifying Incident triggers an automatic read-only investigation.
6. An authorized engineer can investigate an open Incident through Discord.
7. An authorized engineer can investigate a resolved/historical Incident through Discord.
8. The Agent can distinguish historical measurements from current measurements.
9. The Agent can call only registered read-only Backend Tools.
10. Tool calls are authorized and audited by the Backend.
11. Agent produces an evidence-based report with timestamps, evidence, suspected/confirmed RCA distinction, confidence, and recommendations.
12. Discord receives progress and final investigation results in the correct Incident thread.
13. Existing monitoring, State Tracker, alert webhook, reports, and tests continue working.
14. No privileged remediation is possible in the MVP.

## 14. First implementation task

After this architecture document is accepted, start Phase 1 with a focused source audit and design of:

- Incident table and lifecycle rules.
- Incident event mapping from existing alert_events.
- Transactional Outbox schema.
- Outbox delivery contract to the independent Agent Service.

Do not yet add the OpenAI client, Discord Bot, or remediation tools to the existing monitoring application.
