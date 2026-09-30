# Hard fork from hermes-webui

GFIT-CoWork takes the hermes-webui code (nesquena/hermes-webui @ `c296673e`) as its base and will not merge updates from Upstream again. The reason is that we are changing the login system to support many Users and cutting or restricting many features, so merging from Upstream would conflict almost every time. We keep the full original git history, so we can look back at why the original code was written the way it was, and we keep the `LICENSE` file (MIT) with its original copyright notice, as the license requires.

## Consequences

- Bug fixes and security patches from Upstream no longer arrive on their own. When we want one, we pick it one commit at a time (`git cherry-pick`).
- We rename only what Users see, plus the package and repo names. The `HERMES_WEBUI_*` env vars stay as they are, because renaming them is high risk for no benefit. Names that belong to Hermes Agent (`HERMES_HOME`, `hermes_cli`) must not be renamed.
