---
description: Run the full AIPDLC deliver loop for one scoped coding task
---

Deliver the task described in the prompt for this repository.

1. Read enough of the tree to understand how verification works.
2. Plan in `.agent/spec.md` with goal, approach, changes and verification sections.
3. Implement the change and add or update tests using the repository's conventions.
4. Call `run_verification` when the implementation is ready.
5. Call `publish_branch` with a concise commit message.
6. Open a pull request through the Gateway tool for this provider. Use a draft when verification did not pass.
7. Summarise what changed and link the pull request.

Never run `git push` yourself. Never merge or approve pull requests.