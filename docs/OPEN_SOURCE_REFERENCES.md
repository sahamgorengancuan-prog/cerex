# Relevant open-source projects to evaluate (not bundled or vendored)

- **OpenHands / Agent Canvas**: https://github.com/OpenHands/OpenHands . Relevant for coding-agent backends, sandbox orchestration and model-provider integration. The project explicitly warns that unsandboxed host backends have host filesystem access; use container/VM isolation for code-executing agents.
- **Microsoft Agent Framework**: https://github.com/microsoft/agent-framework . Multi-agent and model-provider workflow ideas. Prefer it over archived Microsoft AutoGen for new production integrations.
- **LangGraph**: https://github.com/langchain-ai/langgraph . Durable orchestration/checkpointing and stateful workflow design references.
- **psutil**: https://github.com/giampaolo/psutil . Optional local system telemetry for PC diagnosis (CPU, memory, battery, processes).
- **Microsoft Windows Sandbox**: https://learn.microsoft.com/windows/security/application-security/application-isolation/windows-sandbox/ . Research for disposable isolated GUI workloads; suitability/licensing and automation not assumed.

These references are NOT installed, copied or integrated by this MVP. Licensing and current support status should be reviewed before integrating any library.
