import datetime
import logging
import os
from pathlib import Path
import sys
import time
import keyboard
import pyperclip

# Optional Windows 11 toast notifications
try:
  from winotify import Notification, audio

  HAS_NOTIFICATIONS = True
except ImportError:
  HAS_NOTIFICATIONS = False

# ==========================================
# 1. CONFIGURATION SETTINGS
# ==========================================
HOTKEY_TRIGGER = "ctrl+alt+c"
EXIT_HOTKEY = "ctrl+alt+esc"

# Save directly to a 'QuickNotes' folder on your Windows Desktop
NOTES_DIRECTORY = Path.home() / "Desktop" / "QuickNotes"
FILE_PREFIX = "clip"
DATE_FORMAT = "%Y-%m-%d_%H-%M-%S"
READABLE_DATE_FORMAT = "%B %d, %Y at %I:%M:%S %p"

# Delay (in seconds) to wait for Windows to copy highlighted text
CLIPBOARD_SYNC_DELAY = 0.15
RESTORE_PREVIOUS_CLIPBOARD = True

# ==========================================
# 2. LOGGING & DIRECTORY INITIALIZATION
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def ensure_notes_directory() -> Path:
  """Creates the target directory if it does not already exist."""
  try:
    NOTES_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return NOTES_DIRECTORY
  except Exception as error:
    logging.error(f"Failed to create directory {NOTES_DIRECTORY}: {error}")
    sys.exit(1)


# ==========================================
# 3. WINDOWS NOTIFICATION HELPER
# ==========================================
def send_windows_toast(title: str, message: str) -> None:
  """Displays a native Windows 11 notification banner."""
  if not HAS_NOTIFICATIONS:
    return
  try:
    toast = Notification(
        app_id="QuickClipper MD", title=title, msg=message, duration="short"
    )
    toast.set_audio(audio.Default, loop=False)
    toast.show()
  except Exception as error:
    logging.warning(f"Notification failed: {error}")


# ==========================================
# 4. CLIPBOARD CAPTURE ENGINE
# ==========================================
def capture_selected_text() -> str:
  """Safely copies highlighted text without permanently wiping old clipboard data."""
  previous_clipboard = ""
  if RESTORE_PREVIOUS_CLIPBOARD:
    try:
      previous_clipboard = pyperclip.paste()
    except Exception:
      previous_clipboard = ""

  # Clear clipboard to detect if new text is actually captured
  pyperclip.copy("")
  time.sleep(0.05)

  # Release modifier keys briefly so Ctrl+C registers cleanly
  keyboard.release("alt")
  keyboard.send("ctrl+c")
  time.sleep(CLIPBOARD_SYNC_DELAY)

  captured_text = pyperclip.paste()

  # Restore original clipboard content if enabled
  if RESTORE_PREVIOUS_CLIPBOARD and previous_clipboard:
    pyperclip.copy(previous_clipboard)

  return captured_text
# ==========================================
# 5. MARKDOWN FILE GENERATOR
# ==========================================
def build_markdown_content(text: str, timestamp_obj: datetime.datetime) -> str:
  """Formats the captured text with clean Markdown metadata."""
  readable_time = timestamp_obj.strftime(READABLE_DATE_FORMAT)
  word_count = len(text.split())

  header = (
      f"# Quick Clip — {readable_time}\n\n"
      f"- **Created:** {readable_time}\n"
      f"- **Word Count:** {word_count}\n\n"
      f"---\n\n"
  )
  return header + text.strip() + "\n"


def create_new_markdown_note() -> None:
  """Main workflow triggered when Ctrl+Alt+C is pressed."""
  logging.info("Hotkey detected. Capturing highlighted text...")
  selected_text = capture_selected_text()

  if not selected_text or not selected_text.strip():
    logging.warning("No text was highlighted or clipboard remained empty.")
    send_windows_toast(
        "Nothing Captured", "Highlight some text before pressing Ctrl+Alt+C."
    )
    return

  now = datetime.datetime.now()
  file_timestamp = now.strftime(DATE_FORMAT)
  filename = f"{FILE_PREFIX}_{file_timestamp}.md"
  filepath = ensure_notes_directory() / filename

  markdown_body = build_markdown_content(selected_text, now)

  try:
    with open(filepath, "a", encoding="utf-8") as md_file:
      md_file.write(markdown_body)

    logging.info(f"Saved new note: {filepath}")
    send_windows_toast("Markdown Note Created", f"Saved to {filename}")
  except Exception as error:
    logging.error(f"Could not write Markdown file: {error}")
    send_windows_toast("Save Error", "Failed to write the .md file.")


# ==========================================
# 6. MAIN EVENT LOOP
# ==========================================
def main() -> None:
  ensure_notes_directory()
  logging.info("========================================")
  logging.info("QuickClipper MD is active on Windows 11")
  logging.info(f"Save Folder : {NOTES_DIRECTORY}")
  logging.info(f"New Note    : Press {HOTKEY_TRIGGER.upper()}")
  logging.info(f"Quit Script : Press {EXIT_HOTKEY.upper()}")
  logging.info("========================================")

  keyboard.add_hotkey(HOTKEY_TRIGGER, create_new_markdown_note)
  keyboard.wait(EXIT_HOTKEY)
  logging.info("Exiting QuickClipper MD. Goodbye!")


if __name__ == "__main__":
  main()
