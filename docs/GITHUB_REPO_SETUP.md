# Separate GitHub repository setup

Suggested **private** repository name: `cerex-pc-agentic-core`.

This source package is independent of the existing `sahamgorengancuan-prog/cerex` repository, which represents a distinct CereX web/control-plane codebase. No GitHub repository or branch has been created by this delivery. To preserve source confidentiality, create a private repository in the authenticated GitHub account before pushing.

```bash
cd CereX_PC_Agentic_Core_20261030
git init
git add .
git commit -m "CereX local PC agentic core MVP: diagnostics DAG bus LLM bridge"
# Create a PRIVATE repository named cerex-pc-agentic-core in GitHub first.
git branch -M main
git remote add origin https://github.com/YOUR_OWNER/cerex-pc-agentic-core.git
git push -u origin main
```

Use GitHub Desktop if desired. Do not publish API keys, customer data, PC logs, or real mission output. `.gitignore` excludes local runtime folders and typical credentials; always review `git status` before pushing.

Existing CereX Web UAT stays unchanged. Integrate via a scoped/authenticated device gateway when device pairing, tenancy, and permission review are ready.
