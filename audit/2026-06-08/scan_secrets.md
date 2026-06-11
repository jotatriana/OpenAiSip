# Secret / Credential Scan (OWASP A04, A07, A02)

**Files scanned:** 69  
**Findings:** 1

| Location | Type | Confidence | Evidence |
| --- | --- | --- | --- |
| tests/test_webhook_handler.py:47 | Generic Password Assignment | Review | mock_s.return_value.webhook_secret = "whsec_c29tZXNlY3JldA==" |
