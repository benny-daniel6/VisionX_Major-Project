"""
Google Gemini API Integration.

Provides two cognitive functions:
  1. Agentic Router — selects the best camera to answer a question.
  2. Visual Reasoner — analyzes a camera frame and answers in natural language.
"""
import base64
import json
import logging
import time
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

from backend.config import GOOGLE_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)


class GeminiService:
    """Wraps Google Gemini for routing and visual question answering."""

    def __init__(self):
        if not GOOGLE_API_KEY:
            logger.warning(
                "GOOGLE_API_KEY not set — Gemini calls will fail. "
                "Create a .env file with your key."
            )
        genai.configure(api_key=GOOGLE_API_KEY)
        self._model = genai.GenerativeModel(GEMINI_MODEL)
        logger.info("GeminiService initialised with model=%s", GEMINI_MODEL)

    def _call_with_retry(self, content, max_retries=3):
        """Call generate_content with retry logic for 429 rate limits."""
        for attempt in range(max_retries):
            try:
                return self._model.generate_content(content)
            except ResourceExhausted as e:
                wait = 2 ** (attempt + 1)  # 2s, 4s, 8s
                logger.warning(
                    "Rate limited (429), retrying in %ds (attempt %d/%d)",
                    wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
                if attempt == max_retries - 1:
                    raise
            except Exception:
                raise

    # ── Agentic Router ────────────────────────────────────────────────────

    def route_question(
        self,
        question: str,
        cameras: list[dict],
    ) -> str | None:
        """
        Given a user question and a list of camera metadata dicts,
        ask Gemini to pick the single best camera ID.

        Returns the camera ID string, or None if routing fails.
        """
        if not cameras:
            return None

        camera_descriptions = "\n".join(
            f'  - ID: "{c["id"]}", Name: "{c["name"]}", '
            f'Keywords: {c["keywords"]}'
            for c in cameras
        )

        prompt = f"""You are an intelligent camera routing agent for a multi-camera surveillance system.

Available cameras:
{camera_descriptions}

User question: "{question}"

Your task: Determine which SINGLE camera is most relevant to answer the user's question.
Consider the camera names and keywords to make your decision.
If no camera is clearly relevant, pick the one that is MOST LIKELY to be useful.

Respond with ONLY a JSON object in this exact format (no markdown, no explanation):
{{"camera_id": "<the selected camera id>"}}"""

        try:
            response = self._call_with_retry(prompt)
            text = response.text.strip()
            # Try to parse JSON from the response
            # Handle potential markdown code fences
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            result = json.loads(text)
            cam_id = result.get("camera_id")
            logger.info("Router selected camera_id=%s for question='%s'", cam_id, question[:60])
            return cam_id
        except Exception as e:
            logger.error("Routing failed: %s", e)
            # Fallback: return first camera
            return cameras[0]["id"] if cameras else None

    # ── Visual Reasoner ───────────────────────────────────────────────────

    def analyze_frame(
        self,
        question: str,
        frame_bytes: bytes,
        camera_name: str = "Camera",
    ) -> str:
        """
        Send a JPEG frame + question to Gemini multimodal and return
        a natural-language answer.
        """
        b64_image = base64.b64encode(frame_bytes).decode("utf-8")

        image_part = {
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": b64_image,
            }
        }

        text_prompt = (
            f"You are an AI video surveillance analyst viewing the live feed "
            f"from a camera called '{camera_name}'.\n\n"
            f"User question: \"{question}\"\n\n"
            f"Analyze the image carefully and provide a clear, concise, and "
            f"helpful answer to the user's question based on what you see. "
            f"If you cannot determine the answer from the image, say so honestly. "
            f"Keep your response conversational and under 3 sentences unless "
            f"more detail is specifically needed."
        )

        try:
            response = self._call_with_retry(
                [text_prompt, image_part]
            )
            answer = response.text.strip()
            logger.info(
                "Reasoner answered (cam=%s, q='%s'): %s",
                camera_name,
                question[:40],
                answer[:80],
            )
            return answer
        except Exception as e:
            logger.error("Visual analysis failed: %s", e)
            return f"I'm sorry, I encountered an error analyzing the feed: {str(e)}"

    # ── Question Classifier ──────────────────────────────────────────────

    def classify_question(
        self, question: str, camera_count: int, cameras: list[dict] | None = None,
    ) -> str:
        """
        Determine if a question requires a SINGLE camera or ALL cameras.
        Returns 'SINGLE' or 'ALL'.

        Logic:
          1. If ≤1 camera, always SINGLE.
          2. If the question mentions a specific camera name or keyword,
             it's targeting that camera → SINGLE.
          3. Only fall through to ALL if the query is genuinely about
             all cameras and no specific camera is referenced.
        """
        if camera_count <= 1:
            return "SINGLE"

        q_lower = question.lower()

        # ── Step 1: Check if the question targets a specific camera ─────
        if cameras:
            for cam in cameras:
                # Match against camera name (e.g. "bedroom", "front door")
                cam_name = cam.get("name", "").lower()
                if cam_name and cam_name in q_lower:
                    logger.info(
                        "Classifier: SINGLE (question mentions camera '%s')",
                        cam["name"],
                    )
                    return "SINGLE"

                # Match against any camera keyword (e.g. "bed", "kitchen")
                for kw in cam.get("keywords", []):
                    if kw.lower() in q_lower:
                        logger.info(
                            "Classifier: SINGLE (question mentions keyword '%s' → camera '%s')",
                            kw, cam["name"],
                        )
                        return "SINGLE"

        # ── Step 2: Explicit ALL-cameras intent ─────────────────────────
        # These keywords only trigger ALL mode when no specific camera was matched above
        explicit_all_keywords = [
            "all camera", "every camera", "all feeds", "every feed",
            "everywhere", "everyone", "anyone", "anywhere",
            "whole building", "entire building", "whole office",
            "entire office", "across all", "across the",
            "all rooms", "every room", "whole campus", "entire campus",
        ]
        if any(kw in q_lower for kw in explicit_all_keywords):
            logger.info("Classifier: ALL (explicit all-cameras keyword for '%s')", question[:50])
            return "ALL"

        # ── Step 3: Ambiguous queries — "where is X", "find X", etc. ────
        # These COULD mean "search all cameras" but only if no camera was matched
        search_keywords = [
            "where is", "where are", "where's", "locate",
            "find my", "find the", "search for", "look for",
        ]
        if any(kw in q_lower for kw in search_keywords):
            logger.info("Classifier: ALL (search intent for '%s')", question[:50])
            return "ALL"

        # Default: route to a single camera
        return "SINGLE"

    # ── Multi-Camera Analyzer ────────────────────────────────────────────

    def analyze_all_frames(
        self,
        question: str,
        frames: list[dict],
    ) -> str:
        """
        Send frames from ALL cameras to Gemini in a single multimodal call.

        frames: list of {"name": str, "id": str, "frame_bytes": bytes}

        Returns a synthesized answer that considers all camera feeds.
        """
        if not frames:
            return "No camera frames available for analysis."

        # Build the multimodal content: text prompt + all camera images
        camera_list = ", ".join(f'"{f["name"]}"' for f in frames)
        text_prompt = (
            f"You are an AI video surveillance analyst with access to "
            f"{len(frames)} camera feeds simultaneously: {camera_list}.\n\n"
            f"User question: \"{question}\"\n\n"
            f"I will show you the live frame from each camera in order. "
            f"Analyze ALL frames together to give a comprehensive answer.\n\n"
            f"Guidelines:\n"
            f"- If the question asks for counts (e.g. 'how many people'), "
            f"count from each camera and give individual + total counts.\n"
            f"- If the question asks to find/locate something, check ALL "
            f"cameras and report which camera(s) show what was asked.\n"
            f"- If the question is general, synthesize information from "
            f"all feeds into a unified answer.\n"
            f"- Label each camera's findings clearly.\n"
            f"- Keep the response concise but thorough.\n"
        )

        content = [text_prompt]

        for f in frames:
            # Add a label for each camera
            content.append(f"\n--- Camera: {f['name']} ---\n")
            b64 = base64.b64encode(f["frame_bytes"]).decode("utf-8")
            content.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": b64,
                }
            })

        try:
            response = self._call_with_retry(content)
            answer = response.text.strip()
            logger.info(
                "Multi-cam analysis (q='%s', cams=%d): %s",
                question[:40],
                len(frames),
                answer[:80],
            )
            return answer
        except Exception as e:
            logger.error("Multi-camera analysis failed: %s", e)
            return f"I'm sorry, I encountered an error analyzing the feeds: {str(e)}"
