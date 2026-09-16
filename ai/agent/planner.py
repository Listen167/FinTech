def build_plan(intent: dict) -> dict:
    return {
        "plan_id": "plan_scaffold",
        "nodes": [
            {
                "id": "n1",
                "type": "RESPONSE",
                "input": {"intent": intent},
            }
        ],
    }

