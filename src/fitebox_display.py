#!/usr/bin/env python3
"""
FITEBOX Display Server v1.0
Manage display on HDMI via /dev/fb0.

Protocol (JSON lines):
    {"screen": "ready"}
    {"screen": "failure", "text": "FFmpeg failed"}
    {"text": "Custom message only"}
"""

import json
import os
import signal
import socket
import sys
import textwrap
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from lib import settings

# === CONFIGURATION ===

DISPLAY_SOCKET = os.path.join(settings.RUN_DIR, "fitebox_display.sock")
FRAMEBUFFER = "/dev/fb0"
IMAGE_DIR = Path(os.environ.get("FITEBOX_DISPLAY_IMAGES", "/app/images"))
FONT_DIR = Path(os.environ.get("FITEBOX_FONT_DIR", "/app/fonts"))

# Screen name → image filename mapping
SCREEN_IMAGES = {
    "boot": "img_boot.png",
    "ready": "img_ready.png",
    "recording": "img_recording.png",
    "recording_start": "img_recording_start.png",
    "recording_stop": "img_recording_stop.png",
    "shutdown": "img_shutdown.png",
    "off": "img_off.png",
    "failure": "img_failure.png",
}

# Text rendering
TEXT_FONT_NAME = "DejaVuSansMono.ttf"
TEXT_FONT_SIZE = 50
TEXT_COLOR = (255, 255, 255, 255)  # White
TEXT_Y_PERCENT = 0.85  # Position at 80% of screen height


class FiteboxDisplay:  # pylint: disable=too-many-instance-attributes
    """Framebuffer display server."""

    def __init__(self):
        self.running = True
        self.fb_width = 0
        self.fb_height = 0
        self.fb_bpp = 0
        self.fb_stride = 0
        self.fb_available = False

        # Pre-loaded images (screen_name → bytes ready for fb0)
        self._image_cache: dict[str, bytes] = {}
        self._pil_cache: dict[str, Image.Image] = {}
        self._black_frame: bytes = b""
        self._black_image: Image.Image | None = None
        self._font: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None
        self._font_announce: (
            ImageFont.FreeTypeFont | ImageFont.ImageFont | None
        ) = None
        self._current_screen = ""

        # Overlay (announce) state
        self._overlay_active = False
        self._overlay_flashing = False
        self._overlay_timer: threading.Timer | None = None
        self._overlay_frame_bright: bytes = b""
        self._overlay_frame_dim: bytes = b""
        self._pending_screen: str = ""
        self._pending_text: str = ""

        # Detect framebuffer
        self._detect_framebuffer()
        if self.fb_available:
            self._load_font()
            self._preload_images()
            self._clear_screen()
            print(
                f"📺 Framebuffer ready: {self.fb_width}x{self.fb_height} "
                f"@ {self.fb_bpp}bpp",
            )
        else:
            print("⚠️  No framebuffer available, running in headless mode")

    def _detect_framebuffer(self) -> None:
        """Read framebuffer properties from sysfs."""
        try:
            if not os.path.exists(FRAMEBUFFER):
                return

            fb_sys = Path("/sys/class/graphics/fb0")

            with open(fb_sys / "virtual_size", encoding="utf-8") as f:
                parts = f.read().strip().split(",")
                self.fb_width = int(parts[0])
                self.fb_height = int(parts[1])

            with open(fb_sys / "bits_per_pixel", encoding="utf-8") as f:
                self.fb_bpp = int(f.read().strip())

            with open(fb_sys / "stride", encoding="utf-8") as f:
                self.fb_stride = int(f.read().strip())

            if self.fb_width > 0 and self.fb_height > 0 and self.fb_bpp >= 16:
                self.fb_available = True
        except Exception as e:
            print(f"⚠️  Framebuffer detection failed: {e}")

    def _load_font(self) -> None:
        """Load font for text rendering."""
        font_path = FONT_DIR / TEXT_FONT_NAME
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont | None = None
        try:
            if font_path.exists():
                font = ImageFont.truetype(str(font_path), TEXT_FONT_SIZE)
                print(f"📝 Font loaded: {TEXT_FONT_NAME}")
            else:
                # Try system DejaVu
                for sys_path in [
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                ]:
                    if os.path.exists(sys_path):
                        self._font = ImageFont.truetype(
                            sys_path,
                            TEXT_FONT_SIZE,
                        )
                        print(f"📝 Font loaded: {sys_path}")
                        return
                font = ImageFont.load_default()
                print("📝 Using default font")
        except Exception as e:
            font = ImageFont.load_default()
            print(f"⚠️  Font load error, using default: {e}")
        self._font = font

        # Large font for announcements (3x)
        try:
            if font and hasattr(self._font, "path"):
                # If we have a truetype font, load a larger version
                # for announcements
                font = ImageFont.truetype(
                    self._font.path,
                    TEXT_FONT_SIZE * 3,
                )
        except Exception:
            pass
        self._font_announce = font  # type: ignore[assignment]

    def _image_to_fb_bytes(self, img: Image.Image) -> bytes:
        """Convert PIL Image to framebuffer byte format.

        RPi5 fb0 is typically XRGB8888 (32bpp): bytes are B, G, R, X per pixel.
        """
        # Ensure correct size
        if img.size != (self.fb_width, self.fb_height):
            img = img.resize(
                (self.fb_width, self.fb_height),
                Image.Resampling.LANCZOS,
            )

        img = img.convert("RGBA")

        if self.fb_bpp == 32:
            # RGBA → BGRA (swap R and B channels for framebuffer)
            r, g, b, a = img.split()
            img = Image.merge("RGBA", (b, g, r, a))
            return img.tobytes()

        elif self.fb_bpp == 16:
            # RGB565 - vectorized with numpy
            rgb = np.frombuffer(
                img.convert("RGB").tobytes(),
                dtype=np.uint8,
            ).reshape(-1, 3)
            ch_r = rgb[:, 0].astype(np.uint16)
            ch_g = rgb[:, 1].astype(np.uint16)
            ch_b = rgb[:, 2].astype(np.uint16)
            rgb565 = ((ch_r >> 3) << 11) | ((ch_g >> 2) << 5) | (ch_b >> 3)
            return rgb565.astype("<u2").tobytes()
        else:
            # Fallback: raw RGBA
            return img.tobytes()

    def _center_on_black(self, img: Image.Image) -> Image.Image:
        """Place image centered on a black background of framebuffer size."""
        bg = Image.new("RGBA", (self.fb_width, self.fb_height), (0, 0, 0, 255))
        x = (self.fb_width - img.width) // 2
        y = (self.fb_height - img.height) // 2
        bg.paste(img, (x, y))
        return bg

    def _preload_images(self) -> None:
        """Load all screen images and convert to framebuffer format."""
        # Black frame for clearing
        black = Image.new(
            "RGBA",
            (self.fb_width, self.fb_height),
            (0, 0, 0, 255),
        )
        self._black_frame = self._image_to_fb_bytes(black)
        self._black_image = black

        started = datetime.now()
        for screen_name, filename in SCREEN_IMAGES.items():
            img_path = IMAGE_DIR / filename
            if not img_path.exists():
                print(f"⚠️  Image not found: {img_path}")
                continue
            try:
                img = Image.open(img_path).convert("RGBA")
                # Center on black if not exact fb size
                if img.size != (self.fb_width, self.fb_height):
                    img = self._center_on_black(img)
                self._image_cache[screen_name] = self._image_to_fb_bytes(img)
                self._pil_cache[screen_name] = img
                print(f"  ✅ {screen_name}: {filename}")
            except Exception as e:
                print(f"  ❌ {screen_name}: {e}")

        # Calculate total preload time
        total_seconds = (datetime.now() - started).total_seconds()

        print(
            f"📺 Pre-loaded {len(self._image_cache)} screen images "
            f"in {total_seconds:0.1f} seconds",
        )

    def _render_text_overlay(
        self,
        base_image: Image.Image,
        text: str,
    ) -> bytes:
        """Render text centered at TEXT_Y_PERCENT over a PIL Image."""
        if not self._font:
            return self._image_to_fb_bytes(base_image)

        img = base_image.copy()
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), text, font=self._font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (self.fb_width - tw) // 2
        y = int(self.fb_height * TEXT_Y_PERCENT) - th // 2

        # Shadow for readability
        draw.text((x + 2, y + 2), text, fill=(0, 0, 0, 200), font=self._font)
        draw.text((x, y), text, fill=TEXT_COLOR, font=self._font)

        return self._image_to_fb_bytes(img)

    def _write_fb(self, data: bytes) -> None:
        """Write raw bytes to framebuffer."""
        try:
            with open(FRAMEBUFFER, "wb") as fb:
                fb.write(data)
        except Exception as e:
            print(f"❌ Framebuffer write error: {e}")

    def _clear_screen(self) -> None:
        """Fill screen with black."""
        if self._black_frame:
            self._write_fb(self._black_frame)

    def show_screen(self, screen_name: str, text: str | None = None) -> None:
        if not self.fb_available:
            return
        # During overlay, track state but don't render
        if self._overlay_active:
            self._pending_screen = screen_name
            self._pending_text = text or ""
            self._current_screen = screen_name
            print(
                f"📺 Screen queued (overlay active): {screen_name}"
                + (f" [{text}]" if text else ""),
            )
            return
        frame = self._image_cache.get(screen_name)
        if not frame:
            print(f"⚠️  Unknown screen: {screen_name}")
            if text:
                self.show_text(text)
            return
        if text:
            pil_img = self._pil_cache.get(screen_name)
            if pil_img:
                frame = self._render_text_overlay(pil_img, text)
        self._write_fb(frame)
        self._current_screen = screen_name
        print(f"📺 Screen: {screen_name}" + (f" [{text}]" if text else ""))

    def show_text(self, text: str) -> None:
        if not self.fb_available:
            return
        # During overlay, queue but don't render
        if self._overlay_active:
            self._pending_text = text
            print(f"📺 Text queued (overlay active): {text}")
            return
        base = self._pil_cache.get(self._current_screen) or self._black_image
        if not base:
            return
        frame = self._render_text_overlay(base, text)
        self._write_fb(frame)
        print(f"📺 Text: {text}")

    def show_announce(self, text: str, duration: int = 10) -> None:
        """Show announcement overlay with flashing yellow border,
        auto-scaled text, and yellow bars. Normal messages are queued
        during overlay and restored after."""
        if not self.fb_available or not self._font:
            return

        # Cancel previous overlay/flash
        if self._overlay_timer:
            self._overlay_timer.cancel()
        self._overlay_flashing = False

        # Save current state for restore
        if not self._overlay_active:
            self._pending_screen = self._current_screen
            self._pending_text = ""

        self._overlay_active = True

        # Pre-render both frames (bright and dim border) for flash
        self._overlay_frame_bright = self._render_announce_frame(
            text,
            border_bright=True,
        )
        self._overlay_frame_dim = self._render_announce_frame(
            text,
            border_bright=False,
        )

        # Show bright frame immediately
        self._write_fb(self._overlay_frame_bright)
        print(f"📢 Announce: {text} ({duration}s)")

        # Start flash thread (alternates every 0.5s)
        self._overlay_flashing = True
        flash_thread = threading.Thread(
            target=self._announce_flash_loop,
            daemon=True,
        )
        flash_thread.start()

        # Auto-restore after duration
        self._overlay_timer = threading.Timer(
            duration,
            self._restore_after_overlay,
        )
        self._overlay_timer.daemon = True
        self._overlay_timer.start()

    def _render_announce_frame(
        self,
        text: str,
        border_bright: bool = True,
    ) -> bytes:
        """Render a single announce frame with border and auto-scaled text."""
        bw = 24
        bar_h = 36
        border_color = (
            (232, 160, 32, 255) if border_bright else (80, 55, 10, 255)
        )

        img = Image.new(
            "RGBA",
            (self.fb_width, self.fb_height),
            (10, 10, 20, 255),  # Near-black with slight blue tint
        )
        draw = ImageDraw.Draw(img)

        # Yellow border
        draw.rectangle(
            (0, 0, self.fb_width - 1, self.fb_height - 1),
            outline=border_color,
            width=bw,
        )

        # Yellow bars top and bottom
        bar_color = (232, 160, 32, 255) if border_bright else (80, 55, 10, 255)
        draw.rectangle(
            (0, 0, self.fb_width, bar_h),
            fill=bar_color,
        )
        draw.rectangle(
            (0, self.fb_height - bar_h, self.fb_width, self.fb_height),
            fill=bar_color,
        )

        # Auto-scale text to fit within the available area
        avail_w = self.fb_width - bw * 2 - 40
        avail_h = self.fb_height - bar_h * 2 - 40
        font, lines = self._fit_text(text, avail_w, avail_h)

        # Render centered text block
        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_heights.append(bbox[3] - bbox[1])

        total_h = sum(line_heights) + max(0, len(lines) - 1) * 8
        y = bar_h + (avail_h + 40 - total_h) // 2

        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            x = (self.fb_width - tw) // 2
            # Shadow
            draw.text(
                (x + 3, y + 3),
                line,
                fill=(0, 0, 0, 180),
                font=font,
            )
            draw.text((x, y), line, fill=TEXT_COLOR, font=font)
            y += line_heights[i] + 8

        return self._image_to_fb_bytes(img)

    def _fit_text(
        self,
        text: str,
        max_w: int,
        max_h: int,
    ) -> tuple:
        """Find the largest font size that fits text in the given area.
        Returns (font, list_of_lines)."""
        font_path = None
        if self._font_announce and hasattr(self._font_announce, "path"):
            font_path = self._font_announce.path
        elif self._font and hasattr(self._font, "path"):
            font_path = self._font.path

        if not font_path:
            # Fallback: no scaling possible
            return self._font, [text]  # type: ignore[return-value]

        # Try font sizes from large to small
        for size in range(180, 30, -6):
            try:
                font = ImageFont.truetype(font_path, size)
            except Exception:
                continue

            # Estimate chars per line
            test_bbox = font.getbbox("M")
            char_w = test_bbox[2] - test_bbox[0]
            if char_w <= 0:
                continue
            chars_per_line = max(1, max_w // char_w)

            lines = textwrap.wrap(text, width=chars_per_line)
            if not lines:
                lines = [text]

            # Measure total height
            total_h = 0
            fits = True
            for line in lines:
                bbox = font.getbbox(line)
                lw = bbox[2] - bbox[0]
                lh = bbox[3] - bbox[1]
                if lw > max_w:
                    fits = False
                    break
                total_h += lh + 8
            total_h -= 8  # Remove last gap

            if fits and total_h <= max_h:
                return font, lines

        # Smallest fallback
        font = ImageFont.truetype(font_path, 36)
        lines = textwrap.wrap(text, width=40)
        return font, lines or [text]

    def _announce_flash_loop(self) -> None:
        """Flash between bright and dim border frames."""
        bright = True
        while self._overlay_flashing and self._overlay_active:
            frame = (
                self._overlay_frame_bright
                if bright
                else self._overlay_frame_dim
            )
            if frame:
                self._write_fb(frame)
            bright = not bright
            time.sleep(0.5)

    def _restore_after_overlay(self) -> None:
        """Restore display to pre-overlay state."""
        self._overlay_flashing = False
        self._overlay_active = False
        self._overlay_timer = None
        self._overlay_frame_bright = b""
        self._overlay_frame_dim = b""
        screen = self._pending_screen
        text = self._pending_text
        if screen and screen in self._image_cache:
            self.show_screen(screen, text if text else None)
        print("📢 Overlay ended, display restored")

    # === SOCKET SERVER ===

    def run(self) -> None:
        """Main loop: listen for display commands on Unix socket."""
        print("=" * 50)
        print("  FITEBOX Display Server v1.0")
        print("=" * 50)

        # Clean stale socket
        if os.path.exists(DISPLAY_SOCKET):
            os.unlink(DISPLAY_SOCKET)

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(DISPLAY_SOCKET)
        os.chmod(DISPLAY_SOCKET, 0o600)
        server.listen(5)
        server.settimeout(1.0)

        print(f"✅ Listening on {DISPLAY_SOCKET}")

        # Accept clients in background (messages queue until images ready)
        def _accept_loop():
            while self.running:
                try:
                    client, _ = server.accept()
                    threading.Thread(
                        target=self._handle_client,
                        args=(client,),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        print(f"❌ Accept error: {e}")
                    break

        accept_thread = threading.Thread(target=_accept_loop, daemon=True)
        accept_thread.start()

        # NOW do the slow init (images preload)
        if self.fb_available:
            self._load_font()
            self._preload_images()
            self._clear_screen()
            print(
                f"📺 Framebuffer ready: {self.fb_width}x{self.fb_height} "
                f"@ {self.fb_bpp}bpp",
            )

        # Show boot screen
        if "boot" in self._image_cache:
            self.show_screen("boot")

        # Signal handling
        def _handle_signal(sig, _frame):
            print(f"\n📺 Signal {sig} received, shutting down...")
            self.running = False

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        # Main thread just waits for shutdown
        accept_thread.join()

        # Cleanup
        server.close()
        if os.path.exists(DISPLAY_SOCKET):
            os.unlink(DISPLAY_SOCKET)
        if self.fb_available and "off" in self._image_cache:
            self.show_screen("off")
        print("📺 Display server stopped")

    def _handle_client(self, client: socket.socket) -> None:
        """
        Handle a client connection (short-lived: send message, disconnect).
        """
        buffer = ""
        try:
            while True:
                data = client.recv(4096).decode("utf-8")
                if not data:
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self._process_message(line.strip())
        except Exception:
            pass
        finally:
            client.close()

    def _process_message(self, message: str) -> None:
        """Process a JSON display command."""
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            return

        announce = msg.get("announce")
        screen = msg.get("screen")
        text = msg.get("text")

        if announce:
            duration = msg.get("duration", 10)
            self.show_announce(announce, duration)
        elif screen:
            self.show_screen(screen, text)
        elif text:
            self.show_text(text)
        elif screen == "":
            if (
                self._current_screen
                and self._current_screen in self._image_cache
            ):
                self._write_fb(self._image_cache[self._current_screen])


def _send_to_daemon(msg: dict[str, str]) -> None:
    """CLI: send a message to the running display daemon (retries on boot)."""
    sock_path = os.path.join(
        os.environ.get("FITEBOX_RUN_DIR", "/tmp"),
        "fitebox_display.sock",
    )
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(sock_path)
        s.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        s.close()
        return
    except Exception:
        return


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # CLI mode: fitebox_display.py <screen> [text]
        # Examples:
        #   fitebox_display.py ready
        #   fitebox_display.py failure "FFmpeg crashed"
        #   fitebox_display.py --text "Hello world"
        if sys.argv[1] == "--text":
            _send_to_daemon({"text": " ".join(sys.argv[2:])})
        else:
            msg_to_send: dict[str, str] = {"screen": sys.argv[1]}
            if len(sys.argv) > 2:
                msg_to_send["text"] = " ".join(sys.argv[2:])
            _send_to_daemon(msg_to_send)
    else:
        # Server mode
        display = FiteboxDisplay()
        display.run()
