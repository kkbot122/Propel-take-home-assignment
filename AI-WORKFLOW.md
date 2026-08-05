# AI Workflow

This document explains how AI was used during the project, what I delegated, what I retained ownership of, where the tools failed, and how I evaluated their output.

## Tools Used

I used two primary AI tools throughout the project:

- **GPT-5.6 through Codex** for implementation work, repository-level code generation, refactoring, debugging, test creation, and producing patches.
- **Claude** for system-design discussions, architecture reviews, decomposition of the technical brief, and auditing whether the implementation plan covered the required deliverables.

Both tools were involved from the early architecture phase through the final implementation and debugging stages.

## What I Delegated

I delegated almost all code generation to AI.

This included:

- Backend services and API implementation
- Telemetry-processing logic
- Simulation and reset flows
- Database access code
- Frontend implementation
- Tests
- Docker and deployment configuration
- Refactors and bug-fix patches
- Documentation drafts

I did not manually write the final application code.

My direct work focused on:

- Understanding the technical brief
- Designing the overall system architecture
- Deciding service boundaries and responsibilities
- Designing the operator-console wireframe in Excalidraw
- Breaking the project into implementation stages
- Reviewing generated code and runtime behaviour
- Testing the application manually
- Comparing implementation behaviour against the brief
- Identifying when AI output was incomplete, incorrect, or over-engineered
- Deciding which generated changes were safe to ship

I drew the line there because I treated code generation as a production tool, not as the main intellectual contribution. The part I wanted to own was the system-level reasoning: what should exist, why it should exist, how components should interact, which assumptions were acceptable, and whether the final behaviour matched the problem.

That does not remove the need to understand the code. Since the implementation was AI-generated, I reviewed the important execution paths closely enough to explain their responsibilities, inputs, outputs, state transitions, and failure modes.

## Where the AI Was Wrong

### 1. The simulation reset flow was incomplete

The generated reset implementation appeared correct because the reset endpoint returned `200 OK` and the frontend cleared its local state.

However, the backend did not fully restore all simulator-created states. Some devices marked with simulator-specific status reasons were excluded from the recovery query. In another part of the flow, restoration telemetry was sent to a pole that was already live, while the incident that needed verification remained active.

This created a misleading result:

- The UI reported that the simulation had reset.
- The API request succeeded.
- The database still contained active incidents or unverified restoration state.

I caught this by checking:

- Docker logs for the API and telemetry worker
- The exact pole IDs receiving reset telemetry
- Whether telemetry processing reported `state_changed: true`
- Incident, ticket, restoration, pole-state, and simulated-fault rows directly in PostgreSQL

The key lesson was that a successful HTTP response did not prove that the asynchronous workflow had completed successfully.

### 2. The AI explanation feature kept regenerating output

The first implementation used changing timestamps such as `ticket.updated_at` inside the frontend query key.

The backend updated that timestamp during verification cycles, even when no meaningful ticket state had changed. The frontend therefore treated every polling response as a new explanation request and repeatedly called the AI endpoint.

The code looked reasonable in isolation, but the interaction between polling, cache keys, and backend timestamps caused unnecessary regeneration.

I caught it by tracing repeated requests in the logs and identifying repeated calls to:

```text
POST /api/incidents/{incident_id}/explanation
```

The fix was to make the cache key depend on meaningful incident state rather than volatile timestamps, and to avoid updating ticket timestamps when no semantic state change occurred.

### 3. The system-design response went deeper than the task required

During system-design planning, Claude sometimes expanded the solution beyond the scope of the take-home assignment.

It proposed additional abstractions, edge cases, and production concerns that were technically valid but not necessary for the requested deliverable. Following all of them would have increased implementation time and made the project harder to explain.

I caught this by repeatedly comparing the proposed design against:

- The original technical brief
- The expected user workflow
- The evaluation criteria
- The available implementation time
- Whether each component directly supported a required feature

I discarded or reduced ideas that added complexity without improving the required outcome.

### 4. The first simulation design did not fully reflect the technical brief

The AI initially treated simulation mainly as state mutation and did not model the entire lifecycle carefully enough:

```text
fault injection
→ telemetry generation
→ worker processing
→ incident creation
→ repair claim
→ restoration telemetry
→ verification
→ incident resolution
```

This led to later inconsistencies around reset behaviour, stale devices, restoration anchors, and incident closure.

I caught the issue through end-to-end testing rather than by reading a single function. The individual components often looked correct, but the full state machine did not return to a clean state.

## Estimated AI-Generated Code

My honest estimate is that **approximately 100% of the final code was AI-generated**.

I did not manually author application code. My contribution was in architecture, interaction design, task decomposition, prompting, review, debugging, validation, and deciding what should ship.

This number does not mean I accepted every generated result. A significant part of the work involved rejecting, correcting, or narrowing AI output after testing it against the required behaviour.

## Best Prompts and Sessions

### 1. Turning the project into a testable task list

One of the most useful sessions was asking Claude to convert the design into a detailed implementation plan.

The core instruction was:

> Create a detailed task list for the project. Break the work into tasks that are small enough to implement and test independently. Each task should have a clear outcome and should make the next task possible.

This was valuable because it converted a large architecture into an execution sequence. The first version still missed some deliverables, but a later audit helped identify gaps.

### 2. Auditing the plan against the technical brief

A particularly useful follow-up was asking Claude to review the task list as an evaluator rather than as an implementer.

The intent was:

> Audit the architecture and task list against the original technical brief. Identify missing deliverables, assumptions that are not supported by the brief, areas that are over-engineered, and any workflows that are not covered end to end.

This produced more useful feedback than simply asking for another implementation plan. It exposed missing coverage and helped reduce unnecessary scope.

### 3. Integrating Context7 MCP

For implementation work, the most effective way to ensure good code quality was the Context7 MCP.

A representative instruction was:

> Tasks that require deeper thinking and are documentation heavy, should have a "Refer {particular doc} from context7 MCP"

This reduced unnecessary code generation and followed best practices.

## How I Evaluated AI Output

I did not treat generated code as correct because it compiled or because an endpoint returned success.

I used several checks:

- Compared behaviour against the original brief
- Ran the application end to end
- Inspected Docker service logs
- Verified asynchronous processing in the telemetry worker
- Queried PostgreSQL directly
- Checked whether state transitions were internally consistent
- Reproduced failures after redeployment
- Added or requested regression tests for discovered bugs
- Rejected changes that increased scope without improving the required workflow
- Asked the model to explain important functions and state transitions before accepting them

The main standard I used was:

> Does the complete workflow behave correctly, and can I explain why?

## Final Reflection

AI made it possible to build the project quickly, but it did not remove the need for engineering judgement.

The most difficult problems were not syntax problems. They were failures across boundaries:

- Frontend state versus backend state
- API success versus asynchronous completion
- Simulator state versus device-health state
- Ticket resolution versus incident resolution
- Polling behaviour versus AI-generation caching
- Individual functions versus the full lifecycle

The project reinforced that AI can generate large amounts of plausible code, but plausibility is not the same as correctness. My responsibility was to define the system, test its behaviour, identify contradictions, and decide what was safe to ship.
