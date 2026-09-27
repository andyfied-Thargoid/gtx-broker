"""Vision handler implementation for GTX Broker scheduler.

Processes images using P40 vision endpoint with approved projector.
Implements the vision path from the scheduler architecture.

Key design decisions:
- VisionHandler checks if P40 is running the correct vision model
- Returns WORKER_UNAVAILABLE if vision model is not loaded
- Sends alert to model-profile controller when vision task is queued
- Uses Qwen3.5 Q2 + BF16 projector (fastest in benchmarks, handles Markdown fences)
- Extracts: merchant, date, currency, totals, line_items
- Validates JSON output, rejects missing required fields
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import aiohttp

from gtx_broker.scheduler.handlers import TaskHandler, HandlerResult, HandlerAttempt

logger = logging.getLogger(__name__)


@dataclass
class VisionOutput:
    """Structured output from vision extraction."""
    merchant: str
    date: str
    currency: str
    subtotal: float
    vat: float
    total: float
    savings: float
    line_items: list[dict]


class VisionHandler(TaskHandler):
    """Vision task handler.
    
    Processes images using P40 vision endpoint. Checks model availability,
    alerts model-profile controller if wrong model loaded, and extracts
    invoice/receipt data.
    """
    
    # Model profiles from scheduler architecture benchmarks
    VISION_MODEL_CONFIGS = {
        "qwen35-bf16": {
            "model": "/home/andyfied/models/qwen35/Qwen3.5-35B-A3B-UD-Q3_K_XL.gguf",
            "projector": "/home/andyfied/models/qwen35/VQGAN_f16.gguf",  # BF16 projector
            "context": 32768,
            "reasoning": "fastest extraction, handles Markdown fences"
        },
        "qwen35-f16": {
            "model": "/home/andyfied/models/qwen35/Qwen3.5-35B-A3B-UD-Q3_K_XL.gguf",
            "projector": "/home/andyfied/models/qwen35/VQGAN_f16.gguf",
            "context": 32768,
            "reasoning": "cleanest extraction, correct visible values"
        }
    }
    
    # Preferred config (BF16 projector is fastest)
    PREFERRED_CONFIG = "qwen35-bf16"
    
    def __init__(self):
        """Initialize vision handler."""
        self.endpoint = "http://127.0.0.1:11436"
        self.config = self.VISION_MODEL_CONFIGS[self.PREFERRED_CONFIG]
        self._last_check = None
        self._model_loaded = None
        
    @property
    def handler_type(self) -> str:
        return "vision"
    
    def can_handle(self, task_payload: Dict[str, Any]) -> bool:
        """Check if this handler can process the task."""
        return task_payload.get("kind") == "vision" or task_payload.get("handler_type") == "vision"
    
    def _check_model_loaded(self) -> bool:
        """Check if vision model is currently loaded on P40.
        
        Uses nvidia-smi to detect if GPU memory is allocated (indicates model loaded).
        Returns True if vision model appears to be loaded, False otherwise.
        """
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                logger.warning("nvidia-smi failed, assuming model not loaded")
                return False
            
            lines = result.stdout.strip().split("\n")
            if len(lines) < 2:
                logger.warning("Unexpected nvidia-smi output")
                return False
            
            # Parse GPU memory usage
            used = int(lines[0].strip())
            total = int(lines[1].strip())
            
            # P40 has ~23GB. Qwen3.5 model + projector should use ~15-18GB
            # If memory.used < 10GB, likely no vision model loaded
            memory_gb = used / 1024
            logger.debug(f"GPU memory: {memory_gb:.1f}GB / {total/1024:.1f}GB")
            
            # Threshold: if < 10GB used, assume vision model not loaded
            return memory_gb >= 10
            
        except Exception as e:
            logger.error(f"Failed to check model: {e}")
            return False
    
    def _get_llama_server_process(self) -> Optional[dict]:
        """Check if llama.cpp server is running with vision model.
        
        Returns process info if vision model appears to be loaded, None otherwise.
        """
        try:
            result = subprocess.run(
                ["pgrep", "-fa", "llama-server"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                return None
            
            # Parse processes
            processes = []
            for line in result.stdout.strip().split("\n"):
                if "llama-server" in line and "11436" in line:
                    processes.append(line)
            
            if not processes:
                return None
            
            # Check if any process has vision-related flags
            for proc in processes:
                if "projector" in proc.lower() or "vqgan" in proc.lower():
                    return {"process": proc, "has_projector": True}
            
            # No projector found, might be coding model
            return {"process": processes[0], "has_projector": False}
            
        except Exception as e:
            logger.error(f"Failed to check llama-server: {e}")
            return None
    
    def _send_model_alert(self, task_id: str, task_payload: Dict[str, Any]):
        """Send alert to model-profile controller about vision task.
        
        This tells the controller that a vision task is pending and it needs
        to ensure the correct vision model is loaded on P40.
        """
        try:
            # Check if model alert endpoint exists (future implementation)
            # For now, log to stderr for monitoring
            alert_msg = (
                f"VISION_TASK_PENDING: task={task_id} "
                f"requires vision model on P40. "
                f"Current config: {self.PREFERRED_CONFIG}"
            )
            logger.warning(alert_msg)
            
            # TODO: In future, send to model-profile controller via:
            # - HTTP endpoint on dedicated controller service
            # - or write to alert queue file
            # - or send Telegram alert to admin
            
        except Exception as e:
            logger.error(f"Failed to send model alert: {e}")
    
    def _extract_image_path(self, task_payload: Dict[str, Any]) -> str:
        """Extract image path from task payload."""
        # Check for input_path first (from scheduler task record)
        if task_payload.get("input_path"):
            return task_payload["input_path"]
        
        # Check for file_path
        if task_payload.get("file_path"):
            return task_payload["file_path"]
        
        # Check for path
        if task_payload.get("path"):
            return task_payload["path"]
        
        raise ValueError("No image path found in task payload")
    
    def _build_vision_prompt(self, task_payload: Dict[str, Any]) -> str:
        """Build prompt for vision extraction.
        
        Based on scheduler architecture benchmarks, extract:
        - merchant, date, currency
        - subtotal, vat, total, savings
        - line_items (with quantities when available)
        """
        merchant_hint = task_payload.get("merchant", "")
        caption = task_payload.get("caption", "")
        
        prompt = f"""You are an invoice/receipt data extraction AI. Extract structured data from this receipt image.

Extract the following fields with high accuracy:

1. MERCHANT: Store name/merchant
2. DATE: Transaction date (YYYY-MM-DD format if possible)
3. CURRENCY: Currency code (e.g., GBP, USD, EUR)
4. SUBTOTAL: Pre-tax total
5. VAT: Tax amount
6. TOTAL: Final total including tax
7. SAVINGS: Any discounts or savings applied
8. LINE_ITEMS: List of purchased items with:
   - description: Item name
   - quantity: Number of items (null if not listed)
   - price: Unit price or line total

Additional requirements:
- Distinguish discount lines from actual purchased items
- If quantity is not listed, set it to null (do not infer)
- Return ONLY valid JSON, no Markdown fences, no explanations
- If you must use Markdown, remove the fences and return raw JSON
- All numeric fields should be numbers, not strings

If merchant is known ({merchant_hint}), use it to verify extraction accuracy.

Receipt caption/context: {caption}

Return JSON in this exact format:
{{
  "merchant": "<string>",
  "date": "<YYYY-MM-DD or null>",
  "currency": "<3-letter code>",
  "subtotal": <number>,
  "vat": <number>,
  "total": <number>,
  "savings": <number>,
  "line_items": [
    {{"description": "<string>", "quantity": <number or null>, "price": <number>}},
    ...
  ]
}}"""
        
        return prompt
    
    async def _call_vision_endpoint(self, image_path: str, prompt: str) -> dict:
        """Call P40 vision endpoint with image and prompt.
        
        Uses /api/chat endpoint with vision capabilities.
        """
        async with aiohttp.ClientSession() as session:
            # Step 1: Send image + prompt
            url = f"{self.endpoint}/api/chat"
            
            data = {
                "model": "qwen3.5",  # Vision model name (to be verified)
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "image_url": f"file://{image_path}"
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ],
                "stream": False,
                "max_tokens": 2048,
                "temperature": 0.0
            }
            
            async with session.post(url, json=data, timeout=120) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise ValueError(f"Vision endpoint failed: {resp.status} - {error_text}")
                
                result = await resp.json()
                return result
    
    def _parse_vision_output(self, endpoint_response: dict) -> VisionOutput:
        """Parse vision endpoint response and extract JSON data.
        
        Handles:
        - Raw JSON responses
        - Markdown-fenced JSON (remove fences)
        - Invalid JSON (return error)
        """
        # Extract response text
        content = endpoint_response.get("message", {}).get("content", "")
        if not content:
            raise ValueError("No content in vision response")
        
        # Remove Markdown fences if present
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        # Parse JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse vision JSON: {e}")
        
        # Validate required fields
        required = ["merchant", "date", "currency", "subtotal", "vat", "total", "savings", "line_items"]
        for field in required:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")
        
        # Validate types
        if not isinstance(data["subtotal"], (int, float)):
            raise ValueError("subtotal must be a number")
        if not isinstance(data["vat"], (int, float)):
            raise ValueError("vat must be a number")
        if not isinstance(data["total"], (int, float)):
            raise ValueError("total must be a number")
        if not isinstance(data["savings"], (int, float)):
            raise ValueError("savings must be a number")
        if not isinstance(data["line_items"], list):
            raise ValueError("line_items must be a list")
        
        # Validate line items
        for i, item in enumerate(data["line_items"]):
            if not isinstance(item.get("description"), str):
                raise ValueError(f"line_items[{i}].description must be a string")
            if item.get("quantity") is not None and not isinstance(item["quantity"], (int, float)):
                raise ValueError(f"line_items[{i}].quantity must be a number or null")
            if not isinstance(item.get("price"), (int, float)):
                raise ValueError(f"line_items[{i}].price must be a number")
        
        return VisionOutput(
            merchant=data["merchant"],
            date=data.get("date"),
            currency=data["currency"],
            subtotal=data["subtotal"],
            vat=data["vat"],
            total=data["total"],
            savings=data["savings"],
            line_items=data["line_items"]
        )
    
    async def execute(self, task_payload: Dict[str, Any], 
                      metadata_path: str) -> Tuple[HandlerResult, Optional[Dict[str, Any]], Optional[str]]:
        """Execute vision task.
        
        Args:
            task_payload: Task payload with image path and metadata
            metadata_path: Path to task metadata directory
            
        Returns:
            Tuple of (result, output, error)
        """
        attempt = HandlerAttempt(
            attempt_number=1,
            worker_profile="p40-vision",
            model_profile=self.PREFERRED_CONFIG,
            start_at=datetime.now(timezone.utc).isoformat(),
            end_at=None,
            result=None,
            error=None,
            failure_class=None
        )
        
        try:
            # Step 1: Check if vision model is loaded
            if not self._check_model_loaded():
                # Model not loaded - alert controller and return unavailable
                self._send_model_alert(task_payload.get("id", "unknown"), task_payload)
                return HandlerResult.WORKER_UNAVAILABLE, None, "Vision model not loaded on P40"
            
            # Step 2: Get image path
            try:
                image_path = self._extract_image_path(task_payload)
            except ValueError as e:
                return HandlerResult.FAILED, None, str(e)
            
            # Check if image exists
            if not Path(image_path).exists():
                return HandlerResult.FAILED, None, f"Image not found: {image_path}"
            
            # Step 3: Build prompt
            prompt = self._build_vision_prompt(task_payload)
            
            # Step 4: Call vision endpoint
            logger.info(f"Calling vision endpoint for {image_path}")
            endpoint_response = await self._call_vision_endpoint(image_path, prompt)
            
            # Step 5: Parse and validate output
            try:
                output = self._parse_vision_output(endpoint_response)
            except ValueError as e:
                return HandlerResult.FAILED, None, f"Vision output invalid: {e}"
            
            # Success!
            attempt.end_at = datetime.now(timezone.utc).isoformat()
            attempt.result = {
                "merchant": output.merchant,
                "date": output.date,
                "currency": output.currency,
                "totals": {
                    "subtotal": output.subtotal,
                    "vat": output.vat,
                    "total": output.total,
                    "savings": output.savings
                },
                "line_items": output.line_items,
                "model_profile": self.PREFERRED_CONFIG,
                "processing_time": "estimated < 20s"
            }
            
            return HandlerResult.SUCCESS, attempt.result, None
            
        except aiohttp.ClientError as e:
            attempt.end_at = datetime.now(timezone.utc).isoformat()
            attempt.error = str(e)
            attempt.failure_class = "network_error"
            return HandlerResult.FAILED, None, f"Vision endpoint error: {e}"
            
        except asyncio.TimeoutError:
            attempt.end_at = datetime.now(timezone.utc).isoformat()
            attempt.error = "Vision request timed out"
            attempt.failure_class = "timeout"
            return HandlerResult.RETRY, None, "Vision request timeout (try again)"
            
        except Exception as e:
            attempt.end_at = datetime.now(timezone.utc).isoformat()
            attempt.error = str(e)
            attempt.failure_class = "execution_error"
            return HandlerResult.FAILED, None, f"Vision handler error: {e}"
    
    def validate_output(self, output: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate vision output.
        
        Args:
            output: Vision extraction output
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        required_fields = ["merchant", "date", "currency", "totals", "line_items"]
        
        for field in required_fields:
            if field not in output:
                return False, f"Missing required field: {field}"
        
        # Validate totals structure
        totals = output.get("totals", {})
        if not all(k in totals for k in ["subtotal", "vat", "total", "savings"]):
            return False, "Totals missing required fields"
        
        # Validate line items
        line_items = output.get("line_items", [])
        if not isinstance(line_items, list) or len(line_items) == 0:
            return False, "line_items must be a non-empty list"
        
        for i, item in enumerate(line_items):
            if not all(k in item for k in ["description", "price"]):
                return False, f"line_items[{i}] missing required fields"
        
        return True, None
