import json
import os
import re
import ssl
import socket
import time
import ctypes
import threading
import urllib.request
import subprocess
import tkinter as tk
from tkinter import messagebox, simpledialog
from urllib.parse import quote
from datetime import datetime
from ctypes import wintypes
import pyperclip

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NOTES_DIR = os.path.join(BASE_DIR, "My_AI_Notes")
OFFLINE_DICT_PATH = os.path.join(BASE_DIR, "offline_dict.json")

os.makedirs(NOTES_DIR, exist_ok=True)

FORCE_OFFLINE = False
OFFLINE_CACHE = {}
DNS_CACHE = {}

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

HOTKEY_DICT = 1   # Ctrl + Alt + D (Dictionary + Auto-log to Vocabulary.md)
HOTKEY_CLIP = 2   # Ctrl + Alt + N (Clip highlighted AI note)
HOTKEY_OPEN = 3   # Ctrl + Alt + M (Open/Show Note Manager Window)
HOTKEY_MODE = 4   # Ctrl + Alt + O (Toggle Online/Offline Dictionary)
HOTKEY_QUIT = 5   # Ctrl + Alt + Q (Quit app)

user32 = ctypes.windll.user32

def show_popup(title, message):
    user32.MessageBoxW(0, str(message), str(title), 0x40 | 0x1000)

# ==========================================
# DICTIONARY ENGINE (ONLINE + OFFLINE)
# ==========================================
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
    try:
        url = "https://" + domain + clean_path
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except Exception:
        pass

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

def get_online_definition(word):
    clean_word = quote(word.lower())
    slash = chr(47)

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

# ==========================================
# CLIPBOARD & AUTO-NOTE LOGGING
# ==========================================
def copy_selected_text():
    for _ in range(30):
        alt_down = (user32.GetAsyncKeyState(0x12) & 0x8000) != 0
        d_down = (user32.GetAsyncKeyState(0x44) & 0x8000) != 0
        n_down = (user32.GetAsyncKeyState(0x4E) & 0x8000) != 0
        if not alt_down and not d_down and not n_down:
            break
        time.sleep(0.05)

    old_clip = pyperclip.paste()
    pyperclip.copy("")
    time.sleep(0.05)

    user32.keybd_event(0x11, 0, 0, 0)
    user32.keybd_event(0x43, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(0x43, 0, 2, 0)
    user32.keybd_event(0x11, 0, 2, 0)

    time.sleep(0.15)
    captured = pyperclip.paste().strip()
    pyperclip.copy(old_clip)
    return captured

def log_word_to_vocab(word, definition):
    vocab_file = os.path.join(NOTES_DIR, "Vocabulary.md")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    clean_def = definition.replace("\n\n", " | ")
    entry = f"- **{word.capitalize()}** ({timestamp}): {clean_def}\n"
    try:
        if not os.path.exists(vocab_file):
            with open(vocab_file, "w", encoding="utf-8") as f:
                f.write("# Personal Vocabulary Log\n\n")
        with open(vocab_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass

def handle_dictionary_lookup(ui_refresh_callback):
    selected_text = copy_selected_text()
    if not selected_text:
        show_popup("Dictionary Lookup", "Highlight a word first, then press Ctrl+Alt+D.")
        return

    tokens = selected_text.split()
    first_word = next(iter(tokens), "")
    word = first_word.strip(".,!?;:\"'()[]{}<>-—_")
    if not word:
        return

    if FORCE_OFFLINE:
        offline_def = OFFLINE_CACHE.get(word.lower())
        if offline_def:
            log_word_to_vocab(word, offline_def)
            ui_refresh_callback()
            show_popup(f"{word.capitalize()} (Offline + Saved to Vocabulary.md)", offline_def)
        else:
            show_popup(f"{word.capitalize()} (Offline)", "Word not found in offline dictionary.")
        return

    online_def, net_err = get_online_definition(word)
    if online_def:
        log_word_to_vocab(word, online_def)
        ui_refresh_callback()
        show_popup(f"{word.capitalize()} (Online + Saved to Vocabulary.md)", online_def)
    else:
        offline_def = OFFLINE_CACHE.get(word.lower())
        if offline_def:
            log_word_to_vocab(word, offline_def)
            ui_refresh_callback()
            show_popup(f"{word.capitalize()} (Offline Fallback + Saved)", f"{offline_def}\n\n[Net: {net_err}]")
        else:
            show_popup(f"{word.capitalize()}", f"Definition not found.\n[Net: {net_err}]")

def handle_clip_note(ui_refresh_callback):
    captured = copy_selected_text()
    if not captured:
        show_popup("AI Note Clipper", "Highlight any text or AI response first, then press Ctrl+Alt+N.")
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H:%M:%S")
    daily_file = os.path.join(NOTES_DIR, f"AI_Notes_{today_str}.md")

    try:
        if not os.path.exists(daily_file):
            with open(daily_file, "w", encoding="utf-8") as f:
                f.write(f"# AI Research & Daily Notes ({today_str})\n\n")

        entry = f"### Clipped at {time_str}\n{captured}\n\n---\n\n"
        with open(daily_file, "a", encoding="utf-8") as f:
            f.write(entry)

        ui_refresh_callback()
        preview = captured[:90] + ("..." if len(captured) > 90 else "")
        show_popup("Saved to AI Notes", f"Appended to AI_Notes_{today_str}.md:\n\n\"{preview}\"")
    except Exception as e:
        show_popup("Clipper Error", f"Could not save note: {e}")

# ==========================================
# OBSIDIAN-STYLE DESKTOP NOTE VIEWER
# ==========================================
class NoteWorkspaceUI:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Knowledge & Notes Workspace")
        self.root.geometry("920x600")
        self.root.configure(bg="#1e1e1e")
        self.current_file = None

        # Hide window on close instead of killing background hotkeys
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self.build_ui()
        self.refresh_file_list()

    def build_ui(self):
        # Sidebar
        sidebar = tk.Frame(self.root, width=260, bg="#252526")
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        top_btns = tk.Frame(sidebar, bg="#252526")
        top_btns.pack(fill=tk.X, padx=8, pady=8)

        tk.Button(
            top_btns, text="+ New Topic", command=self.create_topic_note,
            bg="#0e639c", fg="white", relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=8, pady=4
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        tk.Button(
            top_btns, text="Refresh", command=self.refresh_file_list,
            bg="#3c3c3c", fg="white", relief=tk.FLAT, font=("Segoe UI", 9), padx=8, pady=4
        ).pack(side=tk.RIGHT)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.refresh_file_list())
        tk.Entry(
            sidebar, textvariable=self.search_var, bg="#3c3c3c", fg="white",
            insertbackground="white", relief=tk.FLAT, font=("Segoe UI", 10)
        ).pack(fill=tk.X, padx=8, pady=(0, 8), ipady=4)

        self.file_listbox = tk.Listbox(
            sidebar, bg="#252526", fg="#d4d4d4", selectbackground="#094771",
            selectforeground="white", relief=tk.FLAT, font=("Segoe UI", 10),
            activestyle="none", highlightthickness=0
        )
        self.file_listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.file_listbox.bind("<<ListboxSelect>>", self.on_select_file)

        # Shortcut Help Footer
        help_text = (
            "Global Shortcuts:\n"
            "• Ctrl+Alt+N : Clip selected text\n"
            "• Ctrl+Alt+D : Dictionary + Log\n"
            "• Ctrl+Alt+M : Show this window\n"
            "• Ctrl+Alt+Q : Exit completely"
        )
        tk.Label(
            sidebar, text=help_text, bg="#1f1f1f", fg="#9cdcfe",
            justify=tk.LEFT, anchor="w", font=("Consolas", 8), padx=8, pady=8
        ).pack(fill=tk.X, side=tk.BOTTOM)

        # Editor Pane
        editor_pane = tk.Frame(self.root, bg="#1e1e1e")
        editor_pane.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        header = tk.Frame(editor_pane, bg="#1e1e1e")
        header.pack(fill=tk.X, padx=15, pady=(12, 6))

        self.header_label = tk.Label(
            header, text="Select a note on the left", bg="#1e1e1e", fg="white",
            font=("Segoe UI", 13, "bold"), anchor="w"
        )
        self.header_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Button(
            header, text="Open in Notepad++", command=self.open_in_notepad_plus,
            bg="#333333", fg="#cccccc", relief=tk.FLAT, font=("Segoe UI", 9), padx=10, pady=4
        ).pack(side=tk.RIGHT, padx=(6, 0))

        tk.Button(
            header, text="Save Edits (Ctrl+S)", command=self.save_current_file,
            bg="#2e7d32", fg="white", relief=tk.FLAT, font=("Segoe UI", 9), padx=10, pady=4
        ).pack(side=tk.RIGHT)

        self.editor = tk.Text(
            editor_pane, bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
            relief=tk.FLAT, font=("Consolas", 11), wrap=tk.WORD, undo=True
        )
        self.editor.pack(fill=tk.BOTH, expand=True, padx=15, pady=(6, 15))
        self.root.bind("<Control-s>", lambda e: self.save_current_file())

    def refresh_file_list(self):
        query = self.search_var.get().lower().strip()
        self.file_listbox.delete(0, tk.END)
        self.displayed_files = []

        files = sorted(
            [f for f in os.listdir(NOTES_DIR) if f.endswith(".md")],
            key=lambda x: os.path.getmtime(os.path.join(NOTES_DIR, x)),
            reverse=True
        )

        for fname in files:
            fpath = os.path.join(NOTES_DIR, fname)
            match = not query or query in fname.lower()
            if not match:
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        if query in f.read().lower():
                            match = True
                except Exception:
                    pass
            if match:
                self.displayed_files.append(fpath)
                self.file_listbox.insert(tk.END, f"  {fname}")

        if self.current_file and self.current_file in self.displayed_files:
            idx = self.displayed_files.index(self.current_file)
            self.file_listbox.selection_set(idx)
            self.load_file_content(self.current_file)
        elif self.displayed_files and not self.current_file:
            self.file_listbox.selection_set(0)
            self.load_file_content(next(iter(self.displayed_files)))

    def on_select_file(self, event):
        sel = self.file_listbox.curselection()
        if sel:
            idx = next(iter(sel))
            self.load_file_content(self.displayed_files[idx])

    def load_file_content(self, fpath):
        self.current_file = fpath
        self.header_label.config(text=os.path.basename(fpath))
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            self.editor.delete("1.0", tk.END)
            self.editor.insert("1.0", content)
        except Exception as e:
            messagebox.showerror("Read Error", str(e))

    def save_current_file(self):
        if not self.current_file:
            return
        try:
            content = self.editor.get("1.0", tk.END).rstrip("\n") + "\n"
            with open(self.current_file, "w", encoding="utf-8") as f:
                f.write(content)
            self.header_label.config(text=f"{os.path.basename(self.current_file)} (Saved)")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def create_topic_note(self):
        name = simpledialog.askstring("New Topic Note", "Enter topic name (e.g., Python_Tips):")
        if not name:
            return
        clean_name = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).strip().replace(" ", "_")
        if not clean_name:
            return
        fpath = os.path.join(NOTES_DIR, f"{clean_name}.md")
        if not os.path.exists(fpath):
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(f"# {name}\n\n")
        self.current_file = fpath
        self.refresh_file_list()

    def open_in_notepad_plus(self):
        if not self.current_file:
            return
        npp_paths = [
            r"C:\Program Files\Notepad++\notepad++.exe",
            r"C:\Program Files (x86)\Notepad++\notepad++.exe"
        ]
        for exe in npp_paths:
            if os.path.exists(exe):
                subprocess.Popen([exe, self.current_file])
                return
        os.startfile(self.current_file)

    def show_window(self):
        self.refresh_file_list()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_window(self):
        self.root.withdraw()

# ==========================================
# GLOBAL HOTKEY LISTENER THREAD
# ==========================================
def hotkey_listener(app_ui):
    global FORCE_OFFLINE
    if not user32.RegisterHotKey(None, HOTKEY_DICT, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x44):  # D
        show_popup("Hotkey Conflict", "Ctrl+Alt+D is already in use by another script. Close old scripts first!")
        return

    user32.RegisterHotKey(None, HOTKEY_CLIP, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x4E)  # N
    user32.RegisterHotKey(None, HOTKEY_OPEN, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x4D)  # M
    user32.RegisterHotKey(None, HOTKEY_MODE, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x4F)  # O
    user32.RegisterHotKey(None, HOTKEY_QUIT, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x51)  # Q

    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_DICT:
                    handle_dictionary_lookup(lambda: app_ui.root.after(0, app_ui.refresh_file_list))
                elif msg.wParam == HOTKEY_CLIP:
                    handle_clip_note(lambda: app_ui.root.after(0, app_ui.refresh_file_list))
                elif msg.wParam == HOTKEY_OPEN:
                    app_ui.root.after(0, app_ui.show_window)
                elif msg.wParam == HOTKEY_MODE:
                    FORCE_OFFLINE = not FORCE_OFFLINE
                    mode = "OFFLINE ONLY" if FORCE_OFFLINE else "ONLINE FIRST"
                    show_popup("Dictionary Mode", f"Switched to: {mode}")
                elif msg.wParam == HOTKEY_QUIT:
                    app_ui.root.after(0, app_ui.root.destroy)
                    break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        for hk in (HOTKEY_DICT, HOTKEY_CLIP, HOTKEY_OPEN, HOTKEY_MODE, HOTKEY_QUIT):
            user32.UnregisterHotKey(None, hk)

if __name__ == "__main__":
    load_offline_dict()
    root = tk.Tk()
    app = NoteWorkspaceUI(root)

    listener_thread = threading.Thread(target=hotkey_listener, args=(app,), daemon=True)
    listener_thread.start()

    root.mainloop()
