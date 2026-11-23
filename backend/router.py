"""
Local Keyword Camera Routing Module.

Provides offline camera routing using keyword and camera name matching
against metadata for IoT/Edge deployment when the cloud API is unavailable.
"""
import logging

logger = logging.getLogger(__name__)


def route_query_local(question: str, cameras: list[dict]) -> str | None:
    """
    Routes a user query to the best camera using keyword and name matching.
    Returns the matched camera ID string, or None if no match is found.
    """
    if not cameras:
        return None

    # Keyword and name matching fast path
    q_lower = question.lower()
    for cam in cameras:
        cam_name = cam.get("name", "").lower()
        if cam_name and cam_name in q_lower:
            logger.info("Local keyword router matched camera name '%s' -> ID: %s", cam["name"], cam["id"])
            return cam["id"]

        keywords = cam.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",")]
        for kw in keywords:
            if kw and kw.lower() in q_lower:
                logger.info("Local keyword router matched keyword '%s' -> ID: %s", kw, cam["id"])
                return cam["id"]

    logger.info("Local keyword router found no match for query: '%s'", question[:60])
    return None

