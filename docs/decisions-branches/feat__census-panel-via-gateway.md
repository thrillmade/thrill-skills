← back to [docs/timeline.md](../timeline.md)

## 2026-11-16 11:44 - Census panel goes through Vercel AI Gateway: one key (AI_GATEWAY_API_KEY), a fixed address, the old Anthropic key ignored

**Reasoning:** The org's ANTHROPIC_API_KEY is dead: the scheduled census on 2026-11-16 ended red at Panel health with HTTP 401 and the alarm job filed #331. The owner chose a gateway key, which can carry its own spend budget, over replacing the key. The census now reads only AI_GATEWAY_API_KEY, builds the same anthropic client against the constant base URL https://ai-gateway.vercel.sh, and sends the gateway's id for the same model (anthropic/claude-sonnet-5). Reading one key means a repository that still holds only the old secret runs green with the panel off. Before the client exists every ANTHROPIC_* variable is removed from the process environment, because the SDK reads ANTHROPIC_CUSTOM_HEADERS unconditionally and the gateway prefers an Authorization header over the key. The log boundary from #191 is unchanged.

**Alternatives considered:** Replace the Anthropic key: declined by the owner; that key carries no budget of its own., Support both keys: two paths to keep true, and the dead key would still turn the run red., Read the gateway address from the environment: anything able to set a variable could send the key elsewhere., Call the gateway over plain HTTP and drop the SDK: the boundary's failure vocabulary is built on the SDK's exception classes; pinning the SDK install is #329.

**Implications:**
- The panel stays off until the org secret AI_GATEWAY_API_KEY exists and is extended to this repository.
- A gateway spending limit (HTTP 402, documented for budgets) is a red run with its own fixed reason, budget_exceeded.
- Not exercised against the live gateway: no key exists yet, so the first run with the secret is the proof.
- The real-SDK test skips in CI because the test job does not install the SDK; the fake-client test enforces the same rule there. It runs once #329 pins the install.
- Proxy variables are not scrubbed; that is outside the ruled threat (an honest or buggy dependency).

---

