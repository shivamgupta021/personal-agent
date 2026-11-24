# Gmail draft navigation and personal-agent distribution

Research date: 24 November 2026. Recommendations only; no application behavior, OAuth scope, repository visibility or hosted access policy was changed.

## Distribution intent supplied by the owner

The project is a resume/showcase and useful personal assistant. Open-source consumers should clone and run their own instance, connecting their own services if desired. A main assistant can compose grievance and future specialist agents, potentially maintained in separate repositories. A hosted demonstration is a separate distribution mode, not a reason to expose the owner's existing mailbox.

Recommended boundaries: the main runtime owns identity/configuration, credentials, storage, scheduling and Telegram; specialist agents expose versioned Python/service interfaces and own their domain models/prompts/evaluations. Prefer one deployment and database per person initially. Repository separation does not require network microservices. Current single-owner configuration and Keychain suit a local Mac installation; Linux/hosted secret storage still needs a supported backend. Do not claim that cloning currently works unchanged on every operating system.

A future hosted no-Gmail demo can use an explicitly fictional mailbox behind the existing mailbox interface, per-visitor isolated cases, and simulated delivery. Keep public web research real where useful. Gmail creation/navigation cannot be demonstrated as a real action without a connected mailbox; show a labeled simulated draft preview or use a dedicated demo mailbox only under an appropriate access design. Never simply remove TELEGRAM_OWNER_ID filtering on the personal deployment. This is a proposed mode, not existing public access.

## What Google's documented API supplies

A Draft resource contains its immutable draft ID and its Message. It does not expose a documented mobile/web navigation URL. Draft content replacement changes the contained message ID while the draft container remains stable. Consequently, saved links built from a message ID may require refreshing after manual edits. [Draft resource](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts), [draft guide](https://developers.google.com/workspace/gmail/api/guides/drafts).

Our current application constructs an account-qualified Gmail web URL using the stored provider message ID, with mailbox/subject fallback. The explicit `GmailDraftService.open()` path already verifies the draft with Gmail and refreshes its current message ID before returning a link; previously posted Telegram links remain static. It is a web UI navigation convention verified in earlier browser checks, not a documented cross-platform Gmail API contract. See `gmail/drafts.py`, `GmailDraftService.open()` and `GmailDraftService.describe()`.

I did not find an official Google contract for opening an arbitrary existing Gmail draft by draft ID in both Gmail mobile apps. This is a documentation limit, not proof that every unofficial scheme fails. Launching Gmail, composing a new email and opening this existing draft are different acceptance criteria. Do not replace a saved draft with a new `mailto:` composition and claim it opened the original.

## Platform constraints

| Surface | Evidence and product implication |
| --- | --- |
| Telegram web | An HTTPS Gmail link can navigate to Gmail web. Test login, wrong Google account and edited/deleted drafts; retain Drafts/subject fallback. |
| Android | Verified App Links require app/domain association controlled by the app publisher. Chrome supports user-initiated `intent:` launches and fallback URLs, but the target must expose a compatible browsable activity. An app-launch recipe does not establish support for a particular draft. Test Telegram's actual browser rather than extrapolating from Chrome. |
| iOS | Universal Links require association between the website and the target app. If the app is absent, the normal fallback is the website, not automatically the App Store. We cannot associate our domain with Google's Gmail app ourselves. |
| Telegram mobile | Telegram has an in-app browser and user-configurable external-browser behavior. Its Bot API URL buttons document HTTP or tg URLs; arbitrary Gmail/Android schemes should not be assumed portable as button URLs. |

Sources: [Android App Links](https://developer.android.com/training/app-links/about), [Chrome Android intents](https://developer.chrome.com/docs/android/intents), [Apple universal links](https://developer.apple.com/documentation/Xcode/allowing-apps-and-websites-to-link-to-your-content), [Telegram browser](https://telegram.org/blog/w3-browser-mini-app-store), [Telegram browser setting](https://translations.telegram.org/en/android/settings/BrowserSettingsNeverOpenInInfo), [Telegram URL buttons](https://core.telegram.org/bots/api#inlinekeyboardbutton).

## Recommended navigation contract

1. Primary action: **Open draft in Gmail**, using the tested account-qualified web link and refreshed current message ID when practical.
2. Mobile app opening: a best-effort enhancement only after physical iOS/Android tests establish what exact content opens. Preserve an **Open in browser** option.
3. Installation: an explicit **Get Gmail** option leading to Google Play on Android or Apple's App Store on iOS. Do not use a failed timer/intent as proof the app is absent; browser restrictions also prevent launches.
4. Always preserve mailbox and draft-subject fallback. Never claim the draft was opened just because an external application launched.

A public HTTPS helper page could offer platform-aware choices and user-initiated app launch. It cannot manufacture Gmail draft-routing support. It would also introduce a hosted component into local-clone distribution; a phone cannot reach the developer's `localhost` address. Keep direct Gmail HTTPS navigation as the portable baseline. If a helper resolves private draft metadata, require owner authentication and opaque identifiers, without token/email-body URLs or public caching. A static routing-only helper is a different, narrower design and should not fetch private content.

## Prototype acceptance before implementation claims

Test Telegram web, Android and iOS with Gmail installed/uninstalled, correct/wrong Google account, multiple accounts, default versus external Telegram browser, edited draft, deleted/sent draft, and web fallback. Record whether each route opens the exact existing draft, the drafts list, inbox, browser or store. No physical-device validation occurred in this research task.

## Email watching terminology

Scheduled polling and Gmail API `users.watch` are different. Google's push design uses Cloud Pub/Sub, returns change/history identifiers, and requires fetching changes with `history.list`; it does not deliver full email bodies. Watches must be renewed at least every seven days (Google recommends daily). Pub/Sub can push to a webhook or be pulled by a client. This is a possible later hosted optimization, not necessary to demonstrate a reliable local polling worker. [Official Gmail push guide](https://developers.google.com/workspace/gmail/api/guides/push).
