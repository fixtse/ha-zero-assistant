# Zero Assistant

<img src="custom_components/assistant_zero/brand/icon.png" width="128" alt="Zero Assistant Bridge">

Use Zero through Home Assistant Assist, with shared settings, reminders, sensor alerts, and weather alerts. Zero keeps its own prompt, memory, and runtime.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fixtse&repository=ha-zero-assistant&category=integration)

Install through HACS:

1. Open the link above and download the integration.
2. Restart Home Assistant.
3. Add the integration using the button below.

[![Set up integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=assistant_zero)

Start with the Zero instance discovered in **Settings → Devices & services**. Confirm the server URLs, choose the Home Assistant user, then sign in to Zero and approve the connection for an enabled private Zero profile. The configuration flow generates and exchanges both long-lived credentials automatically; no token copying is required. Manual token setup remains available. Under **Configure**, edit Zero’s shared prompt and Home Assistant permissions, including Assist and Frigate service access. Reminder callback settings are in the same integration.

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

### Multiple assistants

Zero exposes every assistant from its catalog as a separate Home Assistant conversation entity, including Salem and custom assistants. Select the desired agent in an Assist pipeline. The catalog refreshes every minute; adding or renaming an assistant does not replace `conversation.zero`, existing pipelines or rule identities. Removed assistants become unavailable. Prompts stay exclusively in Zero, and every agent uses the authenticated HA user’s existing Zero profile, permissions and memory.

Version 1.5.0 requires the matching Zero server for multiple assistants. Older servers retain the existing Zero agent until upgraded.

Shared permissions use a searchable multi-select service list with custom values.
Assist delegation automatically allows `conversation.process`. Frigate is offered
only when Home Assistant has registered `frigate.*` services. Comma-separated
custom service values are split when saved.

## Local Zero account recovery

Zero prefers Telegram recovery when available. For a local Zero admin account with an
existing verified Home Assistant account link and no Telegram destination, version 1.5.2
resolves all current Companion App devices assigned to that user's person under
**Settings → People**. There is no device enrollment code or mobile recovery setup screen.
Changing the person's devices affects the next recovery request automatically. Recovery
codes are still required to reset the Zero password. This does not reset HA credentials.

The authenticated user's private recovery commands resolve notification actions from HA
registry identities and the active mobile notification map. Every matching destination
is attempted once; an uncertain send is not retried. No notifications are sent during
startup or upgrade. Accounts without a linked HA user or Telegram destination require
Zero's console password reset.

## Location alerts

Zero can request zone entry/exit and straight-line distance alerts between people.
Only active alerts subscribe to the selected HA entities; there is no polling or
AI call for monitoring or fixed-message delivery. Both entities must be allowed by
the requesting Zero profile. Creation, resume and restart seed a baseline without
replaying arrivals. Invalid, stale or inaccurate locations break holds without
rearming; recurring distance alerts use a reset margin to reduce GPS jitter.
Phone background location reporting determines detection latency.

Setup and reconfiguration do not ask for a voice satellite. Existing stored
satellite identities remain valid; authenticated Assist/Android users keep their
private bindings. An unconfigured satellite never permits anonymous-device access.
Setup reports missing fields, connectivity, HTTP rejection and a different Zero
public approval origin separately, without automatic retries.

Approve an enabled private user or group in Zero. Group pairing changes only that
group's HA connection, leaving private user bindings untouched. Profiles belonging
to other assistant accounts retain their assistant, permissions and history. The
approval picker supports search and pagination independently of the Users page.
