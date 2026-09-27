"""Scheduler module for task orchestration."""
from .scheduler import Scheduler, SchedulerConfig
from .storage import StorageContract
from .workers import WorkerRegistry, WorkerProfile, WorkerStatus, initialize_workers
from .policies import DailyDispatchPolicy, TaskMode, ScheduleWindow, get_dispatch_policy
from .handlers import (
    TaskHandler,
    HandlerResult,
    VisionHandler,
    CodingHandler,
    ReviewHandler,
    get_handler_for_task,
)
from .epoch_manager import EpochManager
from .quality import ImageQualityGate, QualityAssessment

__all__ = [
    # Core scheduler
    "Scheduler",
    "SchedulerConfig",
    # Storage contract
    "StorageContract",
    # Worker registry
    "WorkerRegistry",
    "WorkerProfile",
    "WorkerStatus",
    "initialize_workers",
    # Dispatch policies
    "DailyDispatchPolicy",
    "TaskMode",
    "ScheduleWindow",
    "get_dispatch_policy",
    "EpochManager",
    "ImageQualityGate",
    "QualityAssessment",
    # Handlers
    "TaskHandler",
    "HandlerResult",
    "VisionHandler",
    "CodingHandler",
    "ReviewHandler",
    "get_handler_for_task",
]
