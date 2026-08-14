"""
Local & Hybrid Camera Routing Module.

Provides routing capabilities using either Gemini API or a local LLM (e.g., DistilGPT2) 
with intelligent keyword and semantic fallback matching for IoT/Edge deployment.
"""
import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

# Lazy loading variables for local Hugging Face transformer router
LOCAL_MODEL_ID = "distilgpt2"
LOCAL_TOKENIZER_ROUTER = None
LOCAL_MODEL_ROUTER = None


def load_local_router_model():
    """
    Loads the local routing model on demand.
    OPTIMIZATION: Uses GPU acceleration if PyTorch with CUDA is available.
    """
    global LOCAL_TOKENIZER_ROUTER, LOCAL_MODEL_ROUTER

    if LOCAL_MODEL_ROUTER is None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            logger.info("Loading local routing model '%s'...", LOCAL_MODEL_ID)
            device = "cuda" if torch.cuda.is_available() else "cpu"

            LOCAL_TOKENIZER_ROUTER = AutoTokenizer.from_pretrained(LOCAL_MODEL_ID)

            if device == "cuda":
                LOCAL_MODEL_ROUTER = AutoModelForCausalLM.from_pretrained(
                    LOCAL_MODEL_ID, torch_dtype=torch.float16
                ).to(device)
                logger.info("Local model loaded on GPU (float16).")
            else:
                LOCAL_MODEL_ROUTER = AutoModelForCausalLM.from_pretrained(LOCAL_MODEL_ID)
                logger.info("Local model loaded on CPU.")

        except Exception as e:
            logger.error("Failed to load local routing model: %s", e)
            return False

    return LOCAL_MODEL_ROUTER is not None


def route_query_local(question: str, cameras: list[dict]) -> str:
    """
    Routes a user query to the best camera using a local LLM with keyword fallback.
    """
    if not cameras:
        return ""

    valid_ids = [cam["id"] for cam in cameras]

    # Keyword matching fast path
    q_lower = question.lower()
    for cam in cameras:
        cam_name = cam.get("name", "").lower()
        if cam_name and cam_name in q_lower:
            logger.info("Local router matched camera name '%s' -> ID: %s", cam["name"], cam["id"])
            return cam["id"]

        keywords = cam.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",")]
        for kw in keywords:
            if kw and kw.lower() in q_lower:
                logger.info("Local router matched keyword '%s' -> ID: %s", kw, cam["id"])
                return cam["id"]

    # Local LLM inference attempt
    if load_local_router_model():
        try:
            import torch
            camera_list_str = "\n".join(
                [f"- ID: \"{c['id']}\", Name: \"{c['name']}\"" for c in cameras]
            )
            prompt = f"Select one camera ID from:\n{camera_list_str}\nQuestion: \"{question}\"\nAnswer ID:"

            device = "cuda" if torch.cuda.is_available() else "cpu"
            inputs = LOCAL_TOKENIZER_ROUTER.encode(prompt, return_tensors="pt").to(device)

            with torch.no_grad():
                outputs = LOCAL_MODEL_ROUTER.generate(
                    inputs,
                    max_new_tokens=10,
                    pad_token_id=LOCAL_TOKENIZER_ROUTER.eos_token_id,
                    temperature=0.3,
                    do_sample=False,
                )

            response_text = LOCAL_TOKENIZER_ROUTER.decode(
                outputs[0][inputs.shape[1]:], skip_special_tokens=True
            ).strip()

            for cam_id in valid_ids:
                if cam_id in response_text:
                    logger.info("Local LLM selected camera: %s", cam_id)
                    return cam_id

        except Exception as e:
            logger.error("Local LLM inference error: %s", e)

    # Default fallback
    logger.warning("No specific match found. Defaulting to first camera: %s", valid_ids[0])
    return valid_ids[0]
