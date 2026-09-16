# Backend

FastAPI service for business APIs, policy enforcement, verification, audit logging, and mock bank data.

Suggested responsibilities:

- API gateway under `/api/v1`
- Mock bank sandbox services
- Permission policy engine
- Confirmation and strong verification workflow
- Audit log creation and query
- Tool endpoints consumed by the AI agent

Run target once dependencies are installed:

```bash
uvicorn app.main:app --reload
```

