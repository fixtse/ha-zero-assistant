# Zero Assistant Bridge

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fixtse&repository=ha-zero-assistant&category=integration)
[![Set up integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=assistant_zero)

HACS preparation is complete, but this repository is currently private. [HACS does
not support private repositories](https://www.hacs.dev/docs/faq/private_repositories/),
so the HACS button cannot download this repository until its owner makes it public.
GitHub login or a personal access token does not remove this HACS limitation.
Until then, use an authenticated GitHub download and the manual installation below.

When the repository is public:

1. With HACS already installed, select **Open in HACS**, choose your Home Assistant instance, and add the custom repository as an **Integration**.
2. Download the integration in HACS, then restart Home Assistant.
3. Select **Set up integration** above, or open Settings → Devices & services → Add integration.
4. To upgrade an existing installation, keep its existing domain and private HA storage; HACS replaces only the component files. Review release notes before updating.

If the HACS button is unavailable, add `https://github.com/fixtse/ha-zero-assistant`
in HACS → ⋮ → Custom repositories, category **Integration**. This also requires
a public repository. The repository uses `custom_components/assistant_zero`,
with its HACS metadata at the repository root. Releases use the manifest's version.


![Integration icon](custom_components/assistant_zero/brand/icon.png)

An optional Home Assistant Assist transport and settings client for the independent Zero assistant.

Private repository: https://github.com/fixtse/ha-zero-assistant. Install `custom_components/assistant_zero` into the same path under your HA config directory, then restart HA and add the integration in Settings → Devices & services. Local icons require Home Assistant 2026.3 or newer. Private repositories require authenticated GitHub access to download updates.

Configure loads Zero's identity prompt and Home Assistant access policy directly from Zero, and Save updates the same authoritative PostgreSQL settings used by the next owner conversation in Telegram or Assist. No duplicate prompt is kept in HA. Reconfigure manages the server URL, voice token, settings token and authorized satellite. The dedicated settings token must be provisioned by Zero; never use its administrator API key. The access master switch blocks new owner HA tool calls. Assist and Frigate require both their individual switch and explicit `domain.service` allowlist entries. `conversation.process` delegates only to the built-in `homeassistant` agent to avoid recursion. Zero still owns its model, memory and queue and can operate without this bridge. Existing HA-owned reminders and callbacks continue independently of the tool access switch.

Existing YAML configuration imports once into a config entry. UI configuration then takes precedence; remove YAML after migration. Preserve existing `.storage` files and integration domains when upgrading. Back up your private config before deployment.

Development: open this repository in an isolated Codespace. Run `python3 -B -m unittest discover -s tests` and `python3 -m compileall -q custom_components`. The parent assistant repository also verifies both real HA config/options flows and protocol compatibility. Source changes must be synchronized with its tested snapshot in `integrations/home_assistant/`. Never run an installer against a live instance as a test.
