"""反诈冷静期服务依赖。"""

from backend.app.services.agent_runtime import runtime
from backend.app.services.fraud_cooling import FraudCoolingService


def get_fraud_cooling_service() -> FraudCoolingService:
    return runtime.fraud_cooling

