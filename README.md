# Zero Assistant

<img src="custom_components/assistant_zero/brand/icon.png" width="128" alt="Zero Assistant Bridge">

Use Zero through Home Assistant Assist, with shared settings, reminders, sensor alerts, and weather alerts. Zero keeps its own prompt, memory, and runtime.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fixtse&repository=ha-zero-assistant&category=integration)

Install through HACS:

1. Open the link above and download the integration.
2. Restart Home Assistant.
3. Add the integration using the button below.

[![Set up integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=assistant_zero)

Start with the Zero instance discovered in **Settings → Devices & services**. Confirm the server URLs, choose the Home Assistant user and satellite, then sign in to Zero and approve the connection for an enabled private Zero profile. The configuration flow generates and exchanges both long-lived credentials automatically; no token copying is required. Manual token setup remains available. Under **Configure**, edit Zero’s shared prompt and Home Assistant permissions, including Assist and Frigate service access. Reminder callback settings are in the same integration.

Requires Home Assistant 2026.3 or newer. HACS must already be installed.

## Android and Home Assistant user identity

In **Settings → Devices & services → Zero Assistant → Configure → Home Assistant
user bindings**, select your Home Assistant account and enter its enabled private
Zero user chat ID from Zero's `/admin` page. This includes administrator accounts:
a phone's device ID does not identify a Zero user. Save once per user; current
bindings appear beside the Home Assistant user names. Leave the chat ID empty to
unlink that user. Zero stores and validates bindings centrally, with revision
checks to prevent overwriting concurrent edits.

Authenticated Assist requests use the bound Zero profile's permissions,
credentials, memory and ordered conversation history, shared with that profile's
Telegram chat. Unbound/inactive users and disabled or removed Zero profiles cannot
submit turns or retrieve results. Binding one HA account to another Zero account
shares that Zero account's conversation; select the intended personal profile.
The configured physical satellite retains its existing owner route when no HA
user identity is supplied. Other devices without an authenticated user are denied.
Existing satellite, entry, pipeline and reminder identities remain unchanged.
Update both the Zero backend and the HACS integration before configuring bindings.

Pairing requests expire after ten minutes. Tokens travel only between the servers; the browser carries the approval request and callback state. Zero stores the HA token encrypted and its own integration token as a digest. Revoke Zero credentials in `/admin` → Security and HA credentials in the selected HA user’s profile. Failed exchanges are not retried. Existing entries, conversation identities and scheduled rules are unchanged. Guided pairing requires the corresponding Zero server update.
