"""GTX broker package.

Provides deterministic routing for the gtx-broker-direct-escalation workflow.
Main entry point is GtxBrokerDirector from controller.py.
"""

from .version import __version__
from .state import (
    ProviderStatus,
    GTXBrokerWorkflow,
    TaskClassification,
    ProviderTransitionType,
    ProviderTransition,
    GTXBrokerState,
    GTXBrokerRegistry,
)
from .classifier import (
    GtxModelConfig,
    GtxModelProfile,
    GtxTaskClassifier,
)
from .escalation_policy import (
    FailureClassification,
    RoutingDecision,
    FailureEvidence,
    EscalationResult,
    GtxEscalationPolicy,
)
from .controller import (
    BrokerStatus,
    RoutingRequest,
    RoutingResponse,
    GtxBrokerDirector,
)
from .api import (
    BacklogExecutionAdapter,
    ExecutionOutcome,
    ExecutionRequest,
    VerificationResult,
    WorkerResult,
    BacklogRunner,
    DispatchOutcome,
    ItemLifecycle,
    RunnerConfig,
)
from .repository_boundary import (
    OwnedRepository,
    RepositoryBoundaryError,
    RepositoryPreflight,
    RepositoryRegistry,
    normalize_remote,
)
from .task_manifest import TaskManifest, TaskManifestError, validate_task_manifest

__all__ = [
    "__version__",
    "ProviderStatus",
    "GTXBrokerWorkflow",
    "TaskClassification",
    "ProviderTransitionType",
    "ProviderTransition",
    "GTXBrokerState",
    "GTXBrokerRegistry",
    "GtxModelConfig",
    "GtxModelProfile",
    "GtxTaskClassifier",
    "FailureClassification",
    "RoutingDecision",
    "FailureEvidence",
    "EscalationResult",
    "GtxEscalationPolicy",
    "BrokerStatus",
    "RoutingRequest",
    "RoutingResponse",
    "GtxBrokerDirector",
    "BacklogExecutionAdapter",
    "ExecutionOutcome",
    "ExecutionRequest",
    "VerificationResult",
    "WorkerResult",
    "BacklogRunner",
    "DispatchOutcome",
    "ItemLifecycle",
    "RunnerConfig",
    "OwnedRepository",
    "RepositoryBoundaryError",
    "RepositoryPreflight",
    "RepositoryRegistry",
    "normalize_remote",
    "TaskManifest",
    "TaskManifestError",
    "validate_task_manifest",
]
