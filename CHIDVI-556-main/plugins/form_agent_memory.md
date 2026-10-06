# CHIDVI-556 Memory-Aware Form Agent

The form agent should use CHIDVI's existing long-term memory system as its source of known user facts rather than creating a second memory database.

## Workflow

1. Open the requested URL.
2. Inspect the visible form and identify fields.
3. Search long-term memory for exact and clear matches.
4. Fill only information that is known with confidence.
5. Narrate meaningful actions.
6. Ask the user when information is missing or ambiguous.
7. Persist useful user-provided answers for future form sessions.
8. Resume from the paused field after the user answers.
9. Verify the completed form before reporting success.
10. Request confirmation immediately before high-consequence final submission such as payment, legal/government, purchases, employment, or college applications.

The agent must never invent personal facts or bypass CAPTCHA, OTP, MFA, passwords, or access controls.
