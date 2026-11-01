from fastapi import APIRouter

from backend.app.api.v1 import (
    agent,
    audit,
    auth,
    channels,
    demo,
    evaluation,
    family_funding,
    fraud_cooling,
    guardian_circle,
    health,
    life_graph,
    life_event_radar,
    operation_passports,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(agent.router, prefix="/agent", tags=["Agent"])
api_router.include_router(
    evaluation.router,
    prefix="/evaluation",
    tags=["Evaluation & Observability"],
)
api_router.include_router(audit.router, prefix="/audit-logs", tags=["Audit"])
api_router.include_router(
    operation_passports.router,
    prefix="/operation-passports",
    tags=["Operation Passport"],
)
api_router.include_router(life_graph.router, prefix="/life-graph", tags=["Life Graph"])
api_router.include_router(
    family_funding.router,
    prefix="/family-funding",
    tags=["Family Funding Negotiation"],
)
api_router.include_router(
    fraud_cooling.router,
    prefix="/fraud-cooling",
    tags=["Fraud Cooling"],
)
api_router.include_router(
    guardian_circle.router,
    prefix="/guardian-circle",
    tags=["Guardian Circle"],
)
api_router.include_router(
    life_event_radar.router,
    prefix="/life-radar",
    tags=["Life Event Radar"],
)
api_router.include_router(channels.router, prefix="/channels", tags=["Channel Gateway"])
api_router.include_router(demo.router, prefix="/demo", tags=["Demo"])

