import subprocess
from enum import Enum
import unicodedata


class PlymouthScreen(str, Enum):
    ready = "ready"
    recording = "recording"
    recording_start = "recording_start"
    recording_stop = "recording_stop"
    shutdown = "shutdown"
    off = "off"
    failure = "failure"


def plymouth_screen(
    screen: PlymouthScreen | None = None, text: str | None = None
):
    """Enviar mensaje a plymouth splash."""
    try:
        if screen:
            subprocess.run(
                ["plymouth", "display-message", f"--text={screen.value}"],
                timeout=2,
                check=True,
            )

        if text:
            subprocess.run(
                ["plymouth", "display-message", f"--text={text}"],
                timeout=2,
                check=True,
            )
        elif screen:
            subprocess.run(
                ["plymouth", "display-message", "--text="],
                timeout=2,
                check=True,
            )

    except Exception:
        pass  # Plymouth may not be active


def clean_text(text):
    """Clean text by removing accents and special characters, converting to pure ASCII."""
    if not text:
        return ""
    # Normalize the text (separates 'é' into 'e' + '´')
    normalized = unicodedata.normalize("NFKD", text)
    # Encode to ASCII, ignoring characters that can't be encoded (like accents), then decode back to string
    return normalized.encode("ascii", "ignore").decode("ascii")
