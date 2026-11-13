import json
import os
import re
import ssl
import socket
import time
import ctypes
import urllib.request
from urllib.parse import quote
from ctypes import wintypes
import pyperclip

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OFFLINE_DICT_PATH = os.path.join(BASE_DIR, "offline_dict.json")

FORCE_OFFLINE = False
OFFLINE_CACHE = {}
DNS_CACHE = {}

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

HOTKEY_LOOKUP = 1  # Ctrl + Alt + D
HOTKEY_MODE = 2    # Ctrl + Alt + O
HOTKEY_QUIT = 3    # Ctrl + Alt + Q

user32 = ctypes.windll.user32

def show_popup(title, message):
    user32.MessageBoxW(0, str(message), str(title), 0x40 | 0x1000)

def resolve_ip_via_doh(domain):
    if domain in DNS_CACHE:
        return DNS_CACHE[domain]

    doh_endpoints = [
        "https://1.1.1" + domain + "&type=A",
        "https://8.8.8" + domain + "&type=A"
    ]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for endpoint in doh_endpoints:
        try:
            req = urllib.request.Request(endpoint, headers={"Accept": "application/dns-json"})
            with urllib.request.urlopen(req, context=ctx, timeout=4) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                for ans in payload.get("Answer", []):
                    if ans.get("type") == 1:
                        ip = ans.get("data", "").strip()
                        if ip:
                            DNS_CACHE[domain] = ip
                            return ip
        except Exception:
            continue
    return None

def https_get_json_sni(domain, path):
    slash = chr(47)
    clean_path = slash + path.lstrip(slash)

    # 1. Try normal HTTPS first
    try:
        url = "https://" + domain + clean_path
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception:
        pass

    # 2. Bypass broken ISP DNS via 1.1.1.1 / 8.8.8.8 and connect directly to IP with SNI
    ip = resolve_ip_via_doh(domain)
    if not ip:
        return None, "Cannot reach 1.1.1.1 or 8.8.8.8"

    try:
        ctx = ssl.create_default_context()
        raw_sock = socket.create_connection((ip, 443), timeout=5)
        ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=domain)

        http_req = (
            f"GET {clean_path} HTTP/1.1\r\n"
            f"Host: {domain}\r\n"
            f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n"
            f"Accept: application/json\r\n"
            f"Connection: close\r\n\r\n"
        )
        ssl_sock.sendall(http_req.encode("utf-8"))

        chunks = []
        while True:
            chunk = ssl_sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        ssl_sock.close()

        raw_response = b"".join(chunks).decode("utf-8", errors="replace")
        header_part, _, body_part = raw_response.partition("\r\n\r\n")

        status_line = next(iter(header_part.splitlines()), "")
        if "200" not in status_line:
            return None, status_line

        if "transfer-encoding: chunked" in header_part.lower():
            decoded_body = []
            idx = 0
            while idx < len(body_part):
                crlf = body_part.find("\r\n", idx)
                if crlf == -1:
                    break
                size_hex = body_part[idx:crlf].split(";").strip()
                if not size_hex:
                    break
                chunk_size = int(size_hex, 16)
                if chunk_size == 0:
                    break
                start = crlf + 2
                decoded_body.append(body_part[start:start + chunk_size])
                idx = start + chunk_size + 2
            body_part = "".join(decoded_body)

        return json.loads(body_part.strip()), None
    except Exception as e:
        return None, str(e)

def load_offline_dict():
    global OFFLINE_CACHE
    if os.path.exists(OFFLINE_DICT_PATH):
        try:
            with open(OFFLINE_DICT_PATH, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
                OFFLINE_CACHE = {k.lower().strip(): v for k, v in raw_data.items()}
        except Exception:
            OFFLINE_CACHE = {}

def copy_selected_text():
    # Wait until fingers lift off Alt and D keys
    for _ in range(30):
        alt_down = (user32.GetAsyncKeyState(0x12) & 0x8000) != 0
        d_down = (user32.GetAsyncKeyState(0x44) & 0x8000) != 0
        if not alt_down and not d_down:
            break
        time.sleep(0.05)

    old_clip = pyperclip.paste()
    pyperclip.copy("")
    time.sleep(0.05)

    user32.keybd_event(0x11, 0, 0, 0)  # Ctrl down
    user32.keybd_event(0x43, 0, 0, 0)  # C down
    time.sleep(0.05)
    user32.keybd_event(0x43, 0, 2, 0)  # C up
    user32.keybd_event(0x11, 0, 2, 0)  # Ctrl up

    time.sleep(0.15)
    captured = pyperclip.paste().strip()
    pyperclip.copy(old_clip)
    return captured

def get_online_definition(word):
    clean_word = quote(word.lower())
    slash = chr(47)

    # 1. Primary Online API: Free Dictionary API
    path1 = slash.join(["api", "v2", "entries", "en", clean_word])
    data, err1 = https_get_json_sni("api.dictionaryapi.dev", path1)
    if isinstance(data, list) and len(data) > 0:
        first_entry = next(iter(data), {})
        meanings = first_entry.get("meanings", [])
        results = []
        for m in meanings:
            pos = m.get("partOfSpeech", "")
            defs = m.get("definitions", [])
            first_def = next(iter(defs), {}) if isinstance(defs, list) else {}
            def_text = first_def.get("definition", "")
            if def_text:
                results.append(f"[{pos}] {def_text}")
            if len(results) >= 2:
                break
        if results:
            return "\n\n".join(results), None

    # 2. Secondary Online API: Wiktionary REST API
    path2 = slash.join(["api", "rest_v1", "page", "definition", clean_word])
    wdata, err2 = https_get_json_sni("en.wiktionary.org", path2)
    if isinstance(wdata, dict):
        en_entries = wdata.get("en", [])
        results = []
        for entry in en_entries:
            pos = entry.get("partOfSpeech", "")
            defs = entry.get("definitions", [])
            first_def = next(iter(defs), {}) if isinstance(defs, list) else {}
            raw_html = first_def.get("definition", "")
            clean_text = re.sub(r"<[^>]+>", "", raw_html).strip()
            if clean_text:
                results.append(f"[{pos}] {clean_text}")
            if len(results) >= 2:
                break
        if results:
            return "\n\n".join(results), None

    return None, f"{err1} | {err2}"

def get_offline_definition(word):
    return OFFLINE_CACHE.get(word.lower(), None)

def lookup_selected_text():
    try:
        selected_text = copy_selected_text()

        if not selected_text:
            show_popup("Dictionary Lookup", "No text captured! Highlight a word first, then press Ctrl+Alt+D.")
            return

        tokens = selected_text.split()
        first_word = next(iter(tokens), "")
        word = first_word.strip(".,!?;:\"'()[]{}<>-—_")
        if not word:
            return

        if FORCE_OFFLINE:
            offline_def = get_offline_definition(word)
            if offline_def:
                show_popup(f"{word.capitalize()} (Offline Mode)", offline_def)
            else:
                show_popup(f"{word.capitalize()} (Offline Mode)", "Word not found in offline dictionary.")
            return

        # Online First
        online_def, net_err = get_online_definition(word)

        if online_def:
            show_popup(f"{word.capitalize()} (Online Dictionary)", online_def)
        else:
            offline_def = get_offline_definition(word)
            if offline_def:
                show_popup(f"{word.capitalize()} (Offline Fallback)", f"{offline_def}\n\n[Net info: {net_err}]")
            else:
                show_popup(f"{word.capitalize()}", f"Definition not found.\n[Net info: {net_err}]")

    except Exception as e:
        show_popup("Dictionary Error", f"Error: {e}")

def toggle_mode():
    global FORCE_OFFLINE
    FORCE_OFFLINE = not FORCE_OFFLINE
    mode_name = "OFFLINE ONLY" if FORCE_OFFLINE else "ONLINE FIRST (with Offline Fallback)"
    show_popup("Dictionary Mode", f"Switched to: {mode_name}")

def main():
    load_offline_dict()
    word_count = len(OFFLINE_CACHE)

    if not user32.RegisterHotKey(None, HOTKEY_LOOKUP, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x44):
        show_popup("Hotkey Conflict", "Windows could not register Ctrl+Alt+D because another program is already using it.")
        return

    user32.RegisterHotKey(None, HOTKEY_MODE, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x4F)
    user32.RegisterHotKey(None, HOTKEY_QUIT, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x51)

    show_popup(
        "Dictionary Lookup Active",
        f"Ready! ({word_count:,} offline words loaded)\n\n"
        "• Ctrl + Alt + D : Look up highlighted word (Online first)\n"
        "• Ctrl + Alt + O : Switch Online-First / Offline-Only\n"
        "• Ctrl + Alt + Q : Quit background app"
    )

    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_LOOKUP:
                    lookup_selected_text()
                elif msg.wParam == HOTKEY_MODE:
                    toggle_mode()
                elif msg.wParam == HOTKEY_QUIT:
                    show_popup("Dictionary Lookup", "Dictionary Lookup service stopped.")
                    break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnregisterHotKey(None, HOTKEY_LOOKUP)
        user32.UnregisterHotKey(None, HOTKEY_MODE)
        user32.UnregisterHotKey(None, HOTKEY_QUIT)

if __name__ == "__main__":
    main()
