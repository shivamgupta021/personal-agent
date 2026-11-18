# Coding standards

## Object-oriented design

The user prefers class-based, object-oriented Python. Apply this to the application's core behavior and new milestones.

- Give each class a clear responsibility: orchestration, tool execution, model transport, session authentication, credential storage, or presentation.
- Keep related state and behavior together. Make the ownership of per-run state explicit; do not accidentally retain one turn's history or execution budget in a reusable service object.
- Prefer composition and constructor injection. The research agent receives its model rather than constructing transport clients internally; additional tool abstractions can wait until real use cases need them.
- Use protocols for genuine interchangeable boundaries, where multiple implementations are actually used. Avoid speculative interfaces or inheritance hierarchies without a concrete need.
- Keep schemas and execution rules with the tool that owns them. Do not expose shared mutable configuration directly.
- Use typed data classes for small result/configuration records.
- Thin process entry points and small private stateless utilities may remain functions. Core application behavior belongs in classes; adding a class should establish responsibility, not merely rename a function.

## Imports

- Put imports at module scope, at the top of the file. Do not use inline imports inside functions, methods, or control-flow branches.

## Testing and data boundaries

- Test observable behavior through public interfaces. Tests should continue to describe the same behavior when internal structure changes.
- Keep real subscription integration checks separate from controlled model/HTTP tests.
- Prefer integrated tools for evaluations: use Langfuse datasets, experiment runners, and scores for model evaluation. Keep custom code focused on application-specific fixtures and checks, and pytest for deterministic regressions. Start with coarse end-to-end checks; add stage-specific evaluators when observed failures justify them.
- Keep credentials outside source control and diagnostic output. Use synthetic data in controlled tests.
- Run typechecking and relevant tests after refactoring. Preserve the agreed milestone scope.

## Configuration, observability, and errors

- Put policy limits, retries, and response-size defaults in `constants.py`; keep agent constructors focused on their actual dependencies.
- Keep prompts separate from agent construction. Prefer framework-owned tracing state to duplicate application status models.
- Configure native Pydantic AI instrumentation once in Django app startup, with content capture disabled by default and an explicit `LANGFUSE_CAPTURE_CONTENT` opt-in for local debugging. Use the standard OpenTelemetry exporter for Langfuse.
- Use module loggers for progress and diagnostics. Reserve CLI prints for user-facing answers, model listings, and interactive sign-in instructions.
- Use Pydantic AI's high-level `run()` unless the application actually needs control over graph nodes.
- Convert recoverable HTTP failures into tool results at the service boundary. Let run failures reach the CLI for sanitized reporting; never log raw provider exceptions or tool arguments.

- Organize features as Django apps. Management commands validate input and call application services; keep entry points thin.
- Give code walkthroughs in chat when requested, rather than adding standalone walkthrough documents.

## Project layout

- Keep `manage.py`, `config/`, and Django application packages at the repository root. Do not add a `src/` wrapper or a package `__main__.py`.
- Use `service.py` for an app's service module. Agent invocation stays behind the application service.
- Use framework-default tool scheduling unless a concrete requirement calls for an override.
