# Coding standards

## Object-oriented design

The user prefers class-based, object-oriented Python. Apply this to the application's core behavior and new milestones.

- Give each class a clear responsibility: orchestration, tool execution, model transport, session authentication, credential storage, or presentation.
- Keep related state and behavior together. Make the ownership of per-run state explicit; do not accidentally retain one turn's history or execution budget in a reusable service object.
- Prefer composition and constructor injection. An agent loop receives its model and tool rather than constructing transport clients internally.
- Use protocols for genuine interchangeable boundaries, such as offline and live models. Avoid speculative interfaces or inheritance hierarchies without a concrete need.
- Keep schemas and execution rules with the tool that owns them. Do not expose shared mutable configuration directly.
- Use typed data classes for small result/configuration records.
- Thin process entry points and small private stateless utilities may remain functions. Core application behavior belongs in classes; adding a class should establish responsibility, not merely rename a function.

## Testing and data boundaries

- Test observable behavior through public interfaces. Tests should continue to describe the same behavior when internal structure changes.
- Keep real subscription integration checks separate from controlled model/HTTP tests.
- Keep credentials outside source control and diagnostic output. Use synthetic data for the learning transcript.
- Run typechecking and relevant tests after refactoring. Preserve the agreed milestone scope.
