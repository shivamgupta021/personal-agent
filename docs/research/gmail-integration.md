# Gmail integration: connection, reading, and draft export

Researched 22 November 2026 against official Google documentation. This is research for a local, owner-only Django assistant, not implementation or authorization to access an inbox. Recommendations below are design choices; cited API behavior is provider documentation. No personal email, credentials, or OAuth project was inspected.

## Scope boundary

| Capability | Evidence and implication |
| --- | --- |
| Search and read email | `gmail.readonly` permits viewing messages and settings; `gmail.metadata` excludes bodies. Both are restricted scopes. [Scope catalog](https://developers.google.com/workspace/gmail/api/auth/scopes) |
| Gmail search query | `messages.list(q=...)` supports Gmail search syntax and explicitly disallows `q` with `gmail.metadata`. Lists return message/thread IDs; fetch details separately. Pagination uses `nextPageToken`; `resultSizeEstimate` is an estimate. [List messages](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list) |
| Save a Gmail draft | `gmail.compose` permits draft management **and sending**. The published scope catalog has no general standalone draft-only scope. `gmail.modify` also permits sending and broader mailbox changes. Add-on scopes are contextual add-on permissions, not a substitute for this local application. [Scope catalog](https://developers.google.com/workspace/gmail/api/auth/scopes) |

Recommendation: use only `gmail.readonly` for connection and reading. Add compose only at the later Gmail-export milestone, after explaining its send authority. Enforce no sending in application code: no send tool, service method, or arbitrary Gmail HTTP operation; test the permitted method set. This restriction cannot make a compose token incapable of sending. An exported local text/MIME draft would avoid compose entirely, but would not create a draft in Gmail.

## Desktop OAuth and lifecycle

Google documents a Desktop client for local CLI applications, system-browser authorization, PKCE, and a loopback callback. Desktop clients are public clients. Installed apps do **not** support incremental authorization. Revocation can invalidate the project's grants across its clients; it can take time to propagate. [Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)

Recommendation: make connection an explicit local management command, bind its temporary callback to loopback only, validate OAuth state, and explicitly enable PKCE. Later scope expansion should perform fresh interactive authorization requesting the complete required scope set (`gmail.readonly` plus `gmail.compose`), validate the granted scopes, then replace stored credentials. Do not assume web-server `include_granted_scopes` behavior applies. Use a dedicated project to limit the impact of disconnect/revocation.

Google's Python quickstart uses `google-api-python-client`, `google-auth-httplib2`, and `google-auth-oauthlib`, including `InstalledAppFlow`, `Credentials`, and refresh through `Request`. Its sample is a simplified testing setup, not a production credential-store design. [Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) The flow library provides `run_local_server`, code-verifier configuration, and requests offline access to obtain a refresh token. [Python flow reference](https://googleapis.dev/python/google-auth-oauthlib/latest/reference/google_auth_oauthlib.flow.html)

Recommendation: place core behavior in classes with separate responsibilities for connection orchestration, Google API access, and credential storage, injected into a thin Django command. Prefer macOS Keychain for this Mac's refresh credentials; this is our storage recommendation, not a Gmail requirement. Keep tokens outside the repository/database/logs/model input. Do not blindly overwrite a usable refresh token when a refresh response omits a new one. Treat revoked/invalid credentials as reconnect-required and show sanitized errors.

External OAuth projects in Testing receive seven-day refresh tokens when Gmail scopes are requested. Other expiration causes include revocation, six months of disuse, password changes involving Gmail scopes, and token-count limits. [OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)

Google lists personal use (only oneself or a few personally known users) as a verification exception; unverified warnings and user caps can still apply. Development/testing is a separate exception. Broader release and server transmission of restricted data need their requirements reviewed; do not generalize the personal-use exception into a public-service approval. [Restricted-scope verification and exceptions](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)

Recommendation: start in Testing and accept reauthorization during learning. Treat publishing status and verification status as distinct decisions; personal use does not itself remove the Testing token lifetime. Revisit Production status with the owner when persistent use becomes necessary. Confirm whether the account is personal Gmail or managed Workspace and whether a dedicated project already exists before configuration.

## Reading and private data

`messages.get` accepts formats and metadata header selection; `gmail.readonly` is an allowed authorization scope. [Get message](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get) Attachment retrieval is a separate endpoint identified by message and attachment IDs. [Get attachment](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments/get)

Recommended first reading slice: owner-requested query → bounded IDs → bounded selected headers/snippets → explicitly selected message text. Cap pages, messages, decoded bytes, and model-visible text. Report truncation. Avoid bulk sync, polling, attachments, and mailbox modifications. Preserve source message/thread IDs with extracted facts. Treat all message bodies, subjects, quoted replies, HTML, and attachments as untrusted evidence, never as instructions or proof of owner approval. Do not automatically follow embedded links or fetch remote images; use bounded plain-text extraction. Attachments require a later explicit retrieval and parsing design.

Google requires accurate disclosure of requested data and use, and consent for changed uses. Its Limited Use rules constrain transfers and use of Google user data. [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy)

Recommendation: distinguish local execution from data staying local. Selected text sent to the assistant's model, Telegram responses, persisted conversation history, and trace content are separate disclosure/retention surfaces. Keep email text and identifiers out of exported traces by default, including tool arguments, exceptions, and attributes, not merely prompt capture. Use synthetic content for tests/evals. Obtain the owner's agreement to the model/Telegram data path before the reading milestone. Define deletion separately for local records, conversation history, backups, exported telemetry, OAuth credentials, and Gmail itself. Disconnect should revoke/remove credentials; it must not silently delete mailbox messages or application evidence.

## Draft export and failure handling

Gmail drafts are containers with stable draft IDs; replacing their content changes the underlying message ID. Creation takes a standards-compliant MIME message encoded as base64url in `message.raw`; updates replace content. Sending is separate and out of scope. [Draft guide](https://developers.google.com/workspace/gmail/api/guides/drafts)

To place a reply draft in an existing thread, supply `draft.message.threadId`, RFC-compliant `References` and `In-Reply-To`, and a matching `Subject`. A thread ID alone is insufficient. [Thread guide](https://developers.google.com/workspace/gmail/api/guides/threads)

Recommendation: create the complaint draft locally first. Export only an owner-approved snapshot with exact sender, To/Cc/Bcc, subject, body, source thread, and attachment list (initially empty). Default to a direct reply, never inferred reply-all. Save the draft ID and the approved content/version hash. Later changes require a new preview. Read an existing draft before overwriting it so owner edits in Gmail are not lost.

The documented `drafts.create` request has no idempotency-key parameter. This is an absence in the API contract, not proof that every retry creates a duplicate. [Create draft](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create) Draft listing supports queries/pagination and returns limited message information; `drafts.get` fetches a selected draft. [List drafts](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/list), [Get draft](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/get)

Recommendation: persist an export operation before calling Gmail. After an ambiguous timeout, mark outcome unknown and reconcile against candidate drafts using a stable application-generated MIME Message-ID plus the approved snapshot, if those properties are retained and searchable in a controlled integration test. Do not claim Message-ID guarantees deduplication. Never blindly retry an uncertain create. If reconciliation is inconclusive, ask the owner to inspect Gmail before another create. Successful export means draft saved, never sent or grievance resolved.

## Proposed first learning milestone

Implement only connection, status, and disconnect through local CLI commands. Connection asks for `gmail.readonly`, completes browser/loopback authorization, stores credentials, and calls `users.getProfile(me)` to verify the expected mailbox. That endpoint returns the email address and mailbox counts and allows readonly access. [Get profile](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile)

Recommended acceptance criteria:

- Expected-account binding rejects an accidentally selected account; granted scopes are checked before storing credentials.
- Restart uses stored credentials; expired access tokens refresh; missing/revoked refresh credentials produce reconnect-required status.
- OAuth cancellation, callback timeout, state mismatch, insufficient scopes, wrong account, and storage failure yield sanitized results.
- Disconnect attempts revocation and deletes local credentials, distinguishing failed remote revocation from successful local removal.
- Synthetic tests exercise the lifecycle; one owner-performed live connection/profile check validates the real setup. No message search, body fetch, model call, Telegram delivery, or draft creation in this slice.
- Pause for review before implementing read-only search, then separately review Gmail draft export.

Decisions for the owner/spec: account type and expected mailbox; existing versus dedicated Cloud project; Testing now versus later Production publishing; Keychain-backed storage; eventual selected-email disclosure to model/Telegram and retention; explicit acceptance that Gmail draft export needs a sending-capable OAuth grant. These are configuration/product decisions, not authorization to send messages.
