# Issue tracker: GitHub

Track work in shivamgupta021/personal-agent, a private repository.
Use the gh CLI from this checkout. Verify the target repository
before creating issues.

- Publish tickets as GitHub issues.
- Read issue bodies, labels, and comments before implementing.
- Use --body-file for multiline issue bodies and comments.
- Record dependencies using native GitHub issue dependencies.
  If unavailable, use a "Blocked by: #N" line.
- Start work only when every blocker is closed.
- Record completion and validation before closing an issue.

The existing product spec remains at
docs/specs/grievance-assistant-mvp.md; link tickets to it.

PRs as a request surface: no.

## Planning and publication

For an invoked to-spec workflow, confirm the highest test seam before publishing.
For to-tickets, present slice granularity and blockers and obtain the user's
approval before publishing or marking implementation tickets ready-for-agent.
Provisional implementation tickets remain needs-triage while decisions are open.

## Wayfinding operations

Use one GitHub issue labelled wayfinder:map as the decision index. Attach
decision issues using native sub-issues; maps and decisions carry only
wayfinder labels, not implementation triage labels.

- Children: `gh api repos/shivamgupta021/personal-agent/issues/NUMBER/sub_issues`.
- Attach: POST to that endpoint with the child's numeric `sub_issue_id`.
- Blockers: GET or POST `repos/shivamgupta021/personal-agent/issues/NUMBER/dependencies/blocked_by`; POST uses the blocking issue's numeric `issue_id`.
- Frontier: open, unassigned children whose native blockers are all closed.
- Claim: assign the selected decision to the authenticated developer before work.
- Resolve: post the resolution, close the decision, then add its linked title
  and a one-line gist to the map's Decisions so far.

Refer to decisions by linked title. Charting creates the map and decisions;
it does not resolve human-in-the-loop questions or start implementation.
