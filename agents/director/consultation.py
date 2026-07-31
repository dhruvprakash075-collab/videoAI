"""consultation.py - ConsultationMixin: user consultation prompts (web UI / CLI).

Extracted verbatim from ``agents/director_agent.py`` (WS-4 mixin split).
"""

import logging
import sys
import threading
import time

from agents.ui_state import UIState

log = logging.getLogger(__name__)


class ConsultationMixin:
    """User consultation flow: single questions, field forms, streaming, decisions."""

    def consult_user(
        self, question: str, options: list[str] | None = None, allow_custom: bool = True
    ) -> str:
        """Consult user via web UI or CLI fallback."""

        # A6: --yes flag — return default without prompting
        if getattr(UIState, "auto_accept", False):
            _default = options[0] if options else "Proceed as planned."
            log.info(f"[DIRECTOR] --yes flag: auto-accepting default for: {question[:60]}")
            return _default

        if hasattr(UIState, "is_ui_mode") and UIState.is_ui_mode:
            UIState.add_log(f"[DIRECTOR PAUSE] {question}")

            UIState.active_question = question

            UIState.status = "paused"

            UIState.pause_event.clear()

            if not UIState.pause_event.wait(timeout=300):
                log.warning("[DIRECTOR] Web UI timeout after 300s — proceeding with default")
                UIState.add_degradation(0, "consult_user", "Web UI timeout after 300s — proceeding with default")

                UIState.status = "running"

                UIState.active_question = None

                return options[0] if options else "Proceed as planned."

            UIState.status = "running"

            UIState.active_question = None

            reply = UIState.user_reply

            UIState.user_reply = None

            return reply or "Proceed as planned."

        def _safe_input(prompt=""):
            try:
                if not sys.stdin.isatty():
                    return None
            except (AttributeError, OSError):
                return None

            try:
                return input(prompt)

            except (EOFError, KeyboardInterrupt):
                print()

                return None

            except Exception:
                # Broken/redirected stdin (e.g. background process on Windows can
                # raise OSError [Errno 22] instead of EOFError). Treat as no input.
                return None

        # Non-interactive run (background process, piped/redirected stdin, no TTY):
        # auto-proceed with the default instead of printing a menu nobody can answer.
        # This prevents the consultation prompts from spinning when run unattended.
        _interactive = True
        try:
            _interactive = sys.stdin.isatty()
        except Exception:
            _interactive = False
        if not _interactive:
            _default = options[0] if options else "Proceed as planned."
            log.info(f"[DIRECTOR] Non-interactive — auto-selecting default for: {question[:60]}")
            return _default

        sep = "=" * 60

        print(f"\n{sep}")

        print("  DIRECTOR CONSULTATION")

        print(sep)

        print(f"\n  {question}\n")

        if options:
            shown = options[:12]  # Show first 12, paginate beyond

            for idx, opt in enumerate(shown, 1):
                print(f"  [{idx}] {opt}")

            if len(options) > 12:
                remaining = len(options) - 12

                print(
                    f"  [{len(shown) + 1}] Show {remaining} more option{'s' if remaining > 1 else ''}..."
                )

            if allow_custom:
                print("  [0] Custom (type your own)")

            print()

            _attempts = 0
            while True:
                _attempts += 1
                if _attempts > 50:
                    log.warning("[DIRECTOR] Too many invalid inputs — using default choice.")
                    return options[0] if options else "Proceed as planned."
                try:
                    choice = _safe_input("  Your choice: ")
                    if choice is None:
                        choice = ""

                    choice = choice.strip()

                    if not choice:
                        return options[0] if options else "Proceed as planned."

                    if choice == "0" and allow_custom:
                        custom_input = _safe_input("  Custom input: ")
                        return custom_input.strip() if custom_input else ""

                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(options):
                            return options[idx]
                        print(
                            f"  Invalid choice. Please enter a number between 1 and {len(options)}, or 0 for custom input."
                        )
                    except ValueError:
                        print("  Invalid input. Please enter a number.")
                except Exception as e:
                    log.warning(f"Input error: {e}. Using default choice.")
                    return options[0] if options else "Proceed as planned."

        else:
            reply = _safe_input("  Your response: ")
            if reply is None:
                return "Proceed with default settings."
            reply = reply.strip()

            return reply if reply else "Proceed as planned."

    def consult_fields(self, fields, vision_summary="", timeout=0, allow_regenerate=False):
        """Present multiple choice fields as a single form.



        Each field dict: {"key": str, "label": str, "current": str, "options": [str], "impact": int}

        User answers with "1:2 3:5" format (field_num:choice_num).

        Empty line = accept all defaults.  'r' = regenerate.

        timeout: if >0, auto-proceed after N seconds (headless/CI).

        """

        # A6: --yes flag — return all defaults without prompting
        if getattr(UIState, "auto_accept", False):
            log.info("[DIRECTOR] --yes flag: auto-accepting all field defaults")
            results = {}
            for f in fields:
                opts = f.get("options", [])
                results[f["key"]] = opts[0] if opts else f.get("current", "")
            return results

        if hasattr(UIState, "is_ui_mode") and UIState.is_ui_mode:
            batch_text = "\n".join(
                "[%d] %s (current: %s)" % (i + 1, f["label"], f["current"])
                for i, f in enumerate(fields)
            )

            UIState.active_question = "Multiple decisions needed:\n" + batch_text

            UIState.status = "paused"

            UIState.pause_event.clear()

            if not UIState.pause_event.wait(timeout=300):
                log.warning("[DIRECTOR] Web UI timeout after 300s — proceeding with default")
                UIState.add_degradation(0, "consult_fields", "Web UI timeout after 300s — proceeding with default")

                UIState.status = "running"

                UIState.active_question = None

                return {}

            UIState.status = "running"

            reply = UIState.user_reply or ""

            UIState.user_reply = None

            results = {}

            for line in reply.strip().split("\n"):
                for part in line.replace(",", " ").split():
                    if ":" in part:
                        try:
                            fi, ci = part.split(":", 1)

                            fi, ci = int(fi) - 1, int(ci) - 1

                            if 0 <= fi < len(fields) and 0 <= ci < len(
                                fields[fi].get("options", [])
                            ):
                                results[fields[fi]["key"]] = fields[fi]["options"][ci]

                        except (ValueError, IndexError):
                            pass

            return results

        def _safe_input(prompt=""):
            try:
                if not sys.stdin.isatty():
                    return None
            except (AttributeError, OSError):
                return None

            try:
                return input(prompt)

            except (EOFError, KeyboardInterrupt):
                print()

                return ""

        sep = "=" * 60

        print("\n" + sep)

        print("  DIRECTOR CONFIGURATION")

        print(sep)

        if vision_summary:
            print(vision_summary)

            print("  " + sep)

        fields = sorted(fields, key=lambda f: f.get("impact", 0), reverse=True)

        for idx, f in enumerate(fields, 1):
            print("\n  [%d] %s" % (idx, f["label"]))

            print("      Director's pick: " + f["current"])

            opts = f.get("options", [])

            if opts:
                for oi, opt in enumerate(opts, 1):
                    print("      %d. %s" % (oi, opt))

            print("      0. Skip (keep default)")

            if allow_regenerate:
                print("      r. Regenerate suggestions")

        print("\n  Quick mode: type '%d' to accept ALL defaults\n" % (len(fields) + 1))

        if timeout > 0:
            user_input: list = [None]

            def _timer():

                time.sleep(timeout)

                if user_input[0] is None:
                    print("\n  [Timeout] Accepting all defaults.")

                    user_input[0] = str(len(fields) + 1)

            t = threading.Thread(target=_timer, daemon=True)

            t.start()

            try:
                line = (
                    _safe_input(
                        "\n  Format: field:choice (e.g. '1:2 3:5') or Enter for all defaults: "
                    )
                    or ""
                ).strip()

            finally:
                user_input[0] = "done"

        else:
            line = (
                _safe_input("\n  Format: field:choice (e.g. '1:2 3:5') or Enter for all defaults: ")
                or ""
            ).strip()

        results = {}

        if not line or line == str(len(fields) + 1):
            for f in fields:
                opts = f.get("options", [])

                results[f["key"]] = opts[0] if opts else f.get("current", "")

            return results

        if line.lower() == "r" and allow_regenerate:
            return {"_regenerate": True}

        for part in line.replace(",", " ").split():
            part = part.strip()

            if not part or ":" not in part:
                continue

            try:
                fi_str, ci_str = part.split(":", 1)

                fi, ci = int(fi_str) - 1, int(ci_str) - 1

                if 0 <= fi < len(fields):
                    opts = fields[fi].get("options", [])

                    if ci == -1:
                        results[fields[fi]["key"]] = (
                            opts[0] if opts else fields[fi].get("current", "")
                        )

                    elif 0 <= ci < len(opts):
                        results[fields[fi]["key"]] = opts[ci]

            except (ValueError, IndexError):
                continue

        for f in fields:
            if f["key"] not in results:
                opts = f.get("options", [])

                results[f["key"]] = opts[0] if opts else f.get("current", "")

        return results

    def consult_user_stream(
        self, question: str, options: list[str] | None = None, allow_custom: bool = True
    ) -> str:
        """Streaming variant: options appear progressively."""
        if hasattr(UIState, "is_ui_mode") and UIState.is_ui_mode:
            import time as _ts

            UIState.add_log(f"[STREAM] {question}")
            if options:
                for i, opt in enumerate(options[:12]):
                    UIState.add_log(f"[OPTION {i + 1}] {opt}")
                    _ts.sleep(0.1)
        return self.consult_user(question, options, allow_custom)

    def consult_on_duration(self, auto_minutes: int) -> dict:
        """Ask user whether to keep, reduce, or adjust video duration."""

        if auto_minutes <= 5:
            return {"accepted": True, "target_minutes": auto_minutes, "action": "keep"}

        h = auto_minutes // 60

        m = auto_minutes % 60

        dur_str = f"{h}h {m}min" if h > 0 else f"{m}min"

        choice = self.consult_user(
            f"Content analysis estimates ~{dur_str} ({auto_minutes} minutes) of video. Would you like to control the duration?",
            options=["Keep estimated duration (Recommended)", "Reduce or adjust the duration"],
        )

        if "keep" in choice.lower() or "recommended" in choice.lower():
            return {"accepted": True, "target_minutes": auto_minutes, "action": "keep"}

        action = self.consult_user("Target duration in minutes?", allow_custom=True)

        try:
            # Guard against non-str / non-numeric types (e.g. empty dict {} on UI timeout)
            if not isinstance(action, (str, int, float)):
                raise TypeError(f"Unexpected action type: {type(action).__name__}")
            target = int(action)

            return {"accepted": True, "target_minutes": target, "action": "adjusted"}

        except (ValueError, TypeError):
            log.warning(
                f"[DURATION] consult_on_duration: could not parse action {action!r} "
                f"(type={type(action).__name__}) — defaulting to 'keep'"
            )
            return {"accepted": True, "target_minutes": auto_minutes, "action": "keep"}

    def ask_cache_ttl(self) -> None:
        """Ask user for cache TTL preference."""

        pass

    def ask_search_online(self) -> bool:
        """Ask user whether to search online for research."""

        choice = self.consult_user(
            "Search online for story context?", options=["No, use story only", "Yes, search online"]
        )

        return "yes" in choice.lower()

    def ask_create_from_scratch(self, topic: str) -> tuple:
        """Ask user if they want to create a story from scratch."""

        choice = self.consult_user(
            f"Create original story for '{topic}'?",
            options=["No, I have a story", "Yes, create from scratch"],
        )

        if "yes" in choice.lower():
            notes = self.consult_user("Any notes for the story?", allow_custom=True)

            return True, notes

        return False, ""
