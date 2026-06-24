# Original User Request

## Initial Request — 2026-06-23T09:14:23Z

# Teamwork Project Prompt — Draft

> Goal: Craft prompt → get user approval → delegate to teamwork_preview

Analyze the `free-claude-code-main` open-source project, identify features missing from `clasp` (such as displaying all provider models directly in Claude Code's `/model` selector), and port those features into the `clasp` codebase.

Working directory: `c:\code\clasp`
Integrity mode: demo

## Requirements

### R1. Analyze missing features
Analyze `free-claude-code-main` (located at `c:\code\clasp\free-claude-code-main`) and compile a list of all features and provider integrations that are missing from `clasp`.

### R2. Port the dynamic model selector
Modify the `GET /v1/models` endpoint in `clasp` so that it exposes all available non-Anthropic models from configured providers, allowing users to select them directly via `/model` in Claude Code. 

### R3. Preserve Architecture & Optimize for Latency
Integrate the new features while strictly preserving `clasp`'s existing Web UI routing architecture. Implementations must aim for the lowest possible latency and represent state-of-the-art performance.

## Acceptance Criteria

### Feature Verification
- [ ] Programmatic: A test or script confirms that calling `GET /v1/models` on the proxy returns a list including non-Anthropic models.
- [ ] Agent-as-judge: An independent review confirms that selecting a non-Anthropic model via `/model` successfully routes the next prompt to that specific model without breaking the Web UI routing logic.
- [ ] Agent-as-judge: An independent review confirms the newly ported code preserves the existing architecture and does not introduce unnecessary latency.

## Follow-up Request — 2026-06-23T20:31:39+05:30

Analyze the `free-claude-code-main` project to identify features missing in the `clasp` project—such as a `/models` feature to view and select available models across providers—and implement them in `clasp` focusing on high performance (lowest ping) and scalability.

Working directory: c:\code\clasp
Integrity mode: development

## Requirements

### R1. Codebase Analysis & Feature Identification
Thoroughly analyze both `clasp` and `free-claude-code-main`. Identify a comprehensive list of features present in `free-claude-code-main` that are missing in `clasp` (including the `/models` command).

### R2. Feature Proposal
Before writing any code for the new features, present the complete list of identified missing features to the user for approval. Do not implement any feature until the user has explicitly approved it.

### R3. Feature Implementation
Implement the approved features natively into `clasp`. The implementation must prioritize best-in-class performance (lowest ping) and high scalability. Do not overly rely on the existing test suite as it is not highly reliable; rely on sound engineering judgment and write new verification methods.

## Acceptance Criteria

### Planning Phase
- [ ] A clear, itemized list of features found in `free-claude-code-main` that are missing from `clasp` is presented to the user.
- [ ] The `/models` feature is prominently included in this list.
- [ ] No implementation begins until the user responds with approval.

### Implementation Phase
- [ ] Approved features are implemented in the `clasp` codebase.
- [ ] Code is demonstrably designed for high scalability and low latency.
- [ ] The implementation includes newly written, reliable tests or programmatic checks to verify functionality independent of the legacy test suite.

## Follow-up — 2026-06-23T15:40:58Z

The user has fully approved the proposal. They said: 'yes, approved; i need your best work'. Proceed with implementing all the proposed features with the highest standards of performance and scalability.
