import gpiod

# Patch to handle differences in gpiod v2.x API across versions
if hasattr(gpiod, "line"):
    # Version v2.2+ (most recent)
    # Enumers are in gpiod.line.Direction and gpiod.line.Bias
    LineSettings = gpiod.LineSettings
    Direction = gpiod.line.Direction
    Bias = gpiod.line.Bias
    Value = gpiod.line.Value
elif hasattr(gpiod, "LineDirection"):
    # Version v2.0-2.1 (intermediate)
    # Enums are in gpiod.LineDirection
    LineSettings = gpiod.LineSettings
    Direction = gpiod.LineDirection
    Bias = gpiod.LineBias
    Value = None  # In this version, request_lines devuelve 0/1 directamente, no enums de Value
else:
    # API v1.x (old) - does not use these for request_lines
    LineSettings = None
    Direction = None
    Bias = None
    Value = None


class FiteboxHardware:
    def __init__(
        self, pin_map, consumer="fitebox_app", debug=False
    ):  # pylint: disable=too-many-branches
        """
        Configure the GPIO buttons by detecting API version and Hardware (RPi 4/5).
        Returns (chip, lines_dict) or (None, None) if it fails.
         - pin_map: List of tuples [(gpio_pin, "ButtonName"), ...]
         - consumer: String identifier for GPIO consumer (default "fitebox_app")
        """  # noqa: E501

        # Store parameters
        self.pin_map = pin_map
        self.consumer = consumer
        self.debug = debug
        self.buttons = {}
        self.chip = None

        # Detect RPi version (only info)
        self.model = None
        try:
            with open("/proc/device-tree/model", "r", encoding="utf8") as f:
                self.model = f.read().strip()
        except Exception:
            pass

        # Chip detection logic tries common paths for both RPi 4 and 5
        chip_path = None
        for path in [
            "/dev/gpiochip4",
            "/dev/gpiochip0",
            "gpiochip4",
            "gpiochip0",
        ]:
            try:
                test_chip = gpiod.Chip(path)
                test_chip.close()  # Only check if it opens
                chip_path = path
                break
            except Exception:
                continue

        if chip_path:
            try:
                self.chip = gpiod.Chip(chip_path)

                for pin, name in pin_map:
                    # --- Logic for API v2.x (New) ---
                    if hasattr(self.chip, "request_lines"):
                        # At v2, request the line with its bias configuration
                        request = self.chip.request_lines(
                            consumer=consumer,
                            config={
                                pin: LineSettings(
                                    direction=Direction.INPUT,
                                    bias=Bias.PULL_UP,
                                )
                            },
                        )
                        self.buttons[name] = {
                            "obj": request,
                            "last_state": 1,
                            "version": 2,
                            "pin": pin,
                        }

                    # --- Logic for API v1.x (Old) ---
                    else:
                        line = self.chip.get_line(pin)
                        line.request(
                            consumer=consumer,
                            type=gpiod.LINE_REQ_DIR_IN,
                            flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP,
                        )
                        self.buttons[name] = {
                            "obj": line,
                            "last_state": 1,
                            "version": 1,
                        }

                    if self.debug:
                        version = self.buttons[name]["version"]
                        print(
                            f"✅ {name} (GPIO {pin}) " f"set [API v{version}]"
                        )

            except Exception as e:
                print(f"❌ Error settings GPIO: {e}")

        # Debug info about hardware
        if debug:
            if self.model:
                if "Raspberry Pi 5" in self.model:
                    print("📋 Hardware: Raspberry Pi 5")
                else:
                    print("📋 Hardware: Raspberry Pi 4 or older")
            else:
                print("📋 Hardware: Unknown")

            if self.chip:
                print(f"🔌 GPIO Chip detectado: {chip_path}")
            else:
                print(
                    "❌ ERROR: no GPIO chip detected. "
                    "Check permissions or hardware."
                )

    def __del__(self):
        """
        Clean up GPIO resources on deletion.
        """
        if hasattr(self, "buttons"):
            for _, button in self.buttons.items():
                try:
                    button["obj"].close()
                except Exception:
                    pass
        if self.chip:
            try:
                self.chip.close()
            except Exception:
                pass

    def read_button(self, name):
        """
        Read the current state of the button by name.
        Returns 0 (pressed), 1 (released), or None if not configured.
        """
        if name in self.buttons:
            return self.read_button_value(self.buttons[name])

        # If button name is not configured
        if self.debug:
            print(f"⚠️ Buutton '{name}' no configured.")
        return None

    def read_button_value(self, button):
        """
        Read the current value (0 or 1) of the button
        Normalize Value.ACTIVE/INACTIVE to 1/0
        """
        if button["version"] == 2:
            value = button["obj"].get_value(button["pin"])

            # In API v2.2+, return Value.ACTIVE or Value.INACTIVE enums
            # Need to convert to 0/1 for consistency
            if Value is not None:
                # v2.2+ with enums
                if value == Value.ACTIVE:
                    return 1
                elif value == Value.INACTIVE:
                    return 0
                else:
                    # Fallback just in case of unexpected value
                    return int(value)
            else:
                # v2.0-2.1 already returns 0/1
                return value
        else:
            # API v1.x returns 0/1 directly
            return button["obj"].get_value()

    def get_button_events(self) -> list[tuple[str, int]]:
        """
        Check all buttons and look up for actions
        return: lista de tuplas (nombre, evento)
            - evento 0 = PRESSED (transición 1 -> 0)
            - evento 1 = RELEASED (transición 0 -> 1)
        """
        events: list[tuple[str, int]] = []
        for name, button in self.buttons.items():
            current_state = self.read_button_value(button)
            if current_state is None:
                continue  # Skip if we can't read the button

            last_state = button["last_state"]

            # Transiction from released (1) to pressed (0)
            if last_state == 1 and current_state == 0:
                events.append((name, 0))  # 0 = PRESSED
                if self.debug:
                    print(f"🔘 {name} PRESSED")

            # Transiction from pressed (0) to released (1)
            elif last_state == 0 and current_state == 1:
                events.append((name, 1))  # 1 = RELEASED
                if self.debug:
                    print(f"🔘 {name} RELEASED")

            button["last_state"] = current_state
        return events

    def button_pressed(self, name):
        """
        Check if a button is currently pressed (active low).
        Returns True if pressed, False if released, or None if not configured.
        """
        state = self.read_button(name)
        if state is None:
            return None
        return state == 0

    def button_released(self, name):
        """
        Check if a button is currently released (active low).
        Returns True if released, False if pressed, or None if not configured.
        """
        state = self.read_button(name)
        if state is None:
            return None
        return state == 1
