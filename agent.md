# Agent Development Constraints

This file applies to all work in this repository.

## Model Responsibilities

1. Planning is owned by `gpt-5.6-sol` at `max` reasoning effort.
   - Read the current request, relevant code, `CONTEXT.md` when present, ADRs,
     plans, types, and repository constraints before proposing work.
   - Produce a bounded implementation plan with explicit acceptance criteria,
     affected files, risks, and verification commands.
   - Do not edit production code during the planning handoff.

2. Implementation is owned by `gpt-5.6-terra` or Luna when that model is
   available on the active platform.
   - Follow the approved plan and existing code patterns.
   - Make the smallest change that satisfies the current acceptance criteria.
   - Stop and return to planning when implementation requires a new
     architectural decision or materially expands scope.

3. Code review is owned by `gpt-5.6-sol`.
   - Review the implemented diff against the approved plan and repository
     constraints.
   - Lead with concrete Critical and Important findings supported by file and
     line references.
   - Keep review read-only. Send required fixes back to the implementation
     model, then perform one focused re-review of the amended diff.

4. Do not turn testing or review into an endless loop.
   - The implementation model runs focused tests while changing behavior and
     runs the required gate once before handoff.
   - The review model does not rerun already evidenced suites unless a named
     code risk requires one focused check.
   - Reuse recorded test output. Do not repeat an unchanged gate.
   - After two unsuccessful fix/review cycles for the same root cause, stop and
     return the architectural conflict to `gpt-5.6-sol` planning instead of
     attempting more speculative patches.

## Development Principles

1. Choose the simplest solution that correctly satisfies the current request
   while respecting repository context, ADRs, knowledge, and documented
   constraints.

2. Do not proactively expand task scope. Do not add abstractions,
   configuration, compatibility layers, or subsystems for hypothetical future
   requirements.

3. Prefer local changes and reuse existing code. Add an abstraction only when
   the current requirement, clear duplication, or an existing architectural
   boundary makes it necessary.

4. For new capabilities, implement one real vertical slice first: connect the
   existing public entry point to the current explicit acceptance output.
   Expand to more scenarios, variants, metrics, or algorithms only after that
   slice is stable. Every step must remain runnable and verifiable.

5. Before implementing a general capability, inspect existing code,
   dependencies, documentation, and type definitions. Add a third-party
   dependency only when it is mature, maintained, and demonstrably reduces
   total complexity or improves reliability.

6. Simplicity must not sacrifice correctness, security boundaries, necessary
   error handling, readability, or testability.

7. Before introducing visible complexity, state why it is necessary and why a
   simpler solution cannot satisfy the current acceptance criteria.

8. Stop after the directly relevant tests and required verification pass. Do
   not extend a completed change into unrelated refactoring or architecture
   work.

## Handoff Contract

Each planning handoff must identify the implementation model, exact scope,
acceptance criteria, and verification gate. Each implementation handoff must
identify the commit or diff, commands run, and unresolved concerns. Each review
must end with either `Approved` or a finite fix list; it must not restart the
planning process without a concrete architectural conflict.

## Default Decision Policy

When work presents multiple valid, non-destructive options, select the
recommended option and continue without asking the user to choose. Resolve
ordinary implementation details using the approved plan, existing repository
patterns, and the simplest sufficient solution. Ask the user only when work
requires new authority, risks irreversible data loss, or depends on external
information that cannot be inferred or safely substituted.
