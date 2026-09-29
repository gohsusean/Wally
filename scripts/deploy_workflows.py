#!/usr/bin/env python3
"""Deploy workflow definitions to n8n.

v0.5: Workflows are configured in config/workflows.yaml and triggered via webhooks.
n8n workflow JSON exports should live in workflows/<name>/.

Full automated deploy is not yet implemented. For now:
  1. Create a Webhook trigger workflow in n8n
  2. Set the webhook path to match config/workflows.yaml (e.g. weekly-backup)
  3. Export JSON to workflows/<name>/workflow.json for version control
  4. Set N8N_WEBHOOK_BASE_URL in .env (e.g. https://n8n.example.com/webhook/)
  5. Enable providers.workflow in config/macbook.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

from wally.config.loader import find_project_root, load_workflows_config


def main() -> int:
    root = find_project_root()
    workflows = load_workflows_config(root)
    if not workflows:
        print("No workflows in config/workflows.yaml")
        return 1

    print("Configured workflows (webhook deploy is manual in v0.5):\n")
    for wf in workflows:
        export_dir = root / "workflows" / wf.name
        export_file = export_dir / "workflow.json"
        status = "exported" if export_file.is_file() else "missing export"
        print(f"  {wf.name}")
        print(f"    webhook_path: {wf.webhook_path}")
        print(f"    action_class: {wf.action_class.value}")
        print(f"    workflows/{wf.name}/workflow.json: {status}")
        print()

    print("Automated n8n deploy is not implemented yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
