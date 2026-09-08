import tkinter as tk
import threading
import time
import sys
import os

try:
    import mss
    import mss.tools
except ImportError:
    os.system("pip install mss -q")
    import mss

try:
    from PIL import Image, ImageTk
except ImportError:
    os.system("pip install Pillow -q")
    from PIL import Image, ImageTk

try:
    import pytesseract
except ImportError:
    os.system("pip install pytesseract -q")
    import pytesseract

try:
    import pyttsx3
except ImportError:
    os.system("pip install pyttsx3 -q")
    import pyttsx3

try:
    import keyboard
except ImportError:
    os.system("pip install keyboard -q")
    import keyboard

# ─── Настройки ───────────────────────────────────────────────
HOTKEY = "f9"
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
OCR_LANG = "rus+eng"
# ──────────────────────────────────────────────────────────────

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

tts_engine = None
tts_lock = threading.Lock()

def init_tts():
    global tts_engine
    tts_engine = pyttsx3.init()
    tts_engine.setProperty("rate", 175)
    voices = tts_engine.getProperty("voices")
    for v in voices:
        if "russian" in v.name.lower() or "ru" in v.id.lower():
            tts_engine.setProperty("voice", v.id)
            break

def speak(text):
    text = text.strip()
    if not text:
        print("[!] Текст не распознан.")
        return
    print(f"[TTS] Озвучиваю: {text[:80]}{'...' if len(text)>80 else ''}")
    def _run():
        with tts_lock:
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            voices = engine.getProperty("voices")
            for v in voices:
                if "russian" in v.name.lower() or "ru" in v.id.lower():
                    engine.setProperty("voice", v.id)
                    break
            engine.say(text)
            engine.runAndWait()
            engine.stop()
    threading.Thread(target=_run, daemon=True).start()


class SelectionOverlay:
    def __init__(self, screenshot_img: Image.Image):
        self.screenshot = screenshot_img
        self.result = None
        self.start_x = self.start_y = 0
        self.end_x = self.end_y = 0
        self.rect_id = None

        self.root = tk.Tk()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.configure(cursor="crosshair")
        self.root.title("Выделите зону")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        self.scale_x = sw / screenshot_img.width
        self.scale_y = sh / screenshot_img.height

        self.canvas = tk.Canvas(self.root, width=sw, height=sh,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        resized = screenshot_img.resize((sw, sh), Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(resized)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)

        self.canvas.create_rectangle(0, 0, sw, 30, fill="#000000")
        self.canvas.create_text(sw//2, 15, fill="#ffffff", font=("Arial", 12),
            text="Выделите зону мышью → нажмите Enter  |  Esc — отмена")

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Return>", self._on_enter)
        self.root.bind("<Escape>", self._on_escape)

    def _on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        self.end_x, self.end_y = event.x, event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)

    def _on_drag(self, event):
        self.end_x, self.end_y = event.x, event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, event.x, event.y,
            outline="#ff0000", width=2, dash=(4, 2))

    def _on_release(self, event):
        self.end_x, self.end_y = event.x, event.y

    def _on_enter(self, event):
        x1 = min(self.start_x, self.end_x)
        y1 = min(self.start_y, self.end_y)
        x2 = max(self.start_x, self.end_x)
        y2 = max(self.start_y, self.end_y)
        if x2 - x1 < 5 or y2 - y1 < 5:
            return
        ox1 = int(x1 / self.scale_x)
        oy1 = int(y1 / self.scale_y)
        ox2 = int(x2 / self.scale_x)
        oy2 = int(y2 / self.scale_y)
        self.result = (ox1, oy1, ox2, oy2)
        self.root.destroy()

    def _on_escape(self, event):
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result


def take_screenshot() -> Image.Image:
    # Используем mss.MSS() вместо устаревшего mss.mss()
    with mss.MSS() as sct:
        monitor = sct.monitors[0]
        sct_img = sct.grab(monitor)
        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")


def ocr_region(img: Image.Image, region: tuple) -> str:
    x1, y1, x2, y2 = region
    cropped = img.crop((x1, y1, x2, y2))
    w, h = cropped.size
    cropped = cropped.resize((w * 2, h * 2), Image.LANCZOS)
    text = pytesseract.image_to_string(cropped, lang=OCR_LANG, config="--psm 6")
    return text


def trigger():
    """Вызывается по горячей клавише — запускает overlay в главном потоке."""
    print(f"\n[{HOTKEY.upper()}] Делаю скриншот...")
    time.sleep(0.2)
    img = take_screenshot()
    print("[OK] Скриншот готов. Открываю окно выделения...")

    overlay = SelectionOverlay(img)
    region = overlay.run()

    if region is None:
        print("[!] Выделение отменено.")
        return

    print(f"[OCR] Распознаю текст в зоне {region}...")
    text = ocr_region(img, region)

    if not text.strip():
        print("[!] Текст не найден в выделенной зоне.")
    else:
        speak(text)


def hotkey_listener():
    """
    Слушает горячую клавишу в отдельном потоке.
    Вместо keyboard.wait() используем бесконечный цикл с read_event —
    это избегает OSError access violation при совместной работе с tkinter.
    """
    keyboard.add_hotkey(HOTKEY, lambda: threading.Thread(target=trigger, daemon=True).start())
    while True:
        try:
            keyboard.read_event(suppress=False)
        except Exception:
            time.sleep(0.05)


def main():
    print("=" * 50)
    print("  Screen Reader — Озвучка текста с экрана")
    print("=" * 50)
    print(f"  Горячая клавиша : {HOTKEY.upper()}")
    print(f"  Языки OCR       : {OCR_LANG}")
    print("=" * 50)
    print("  Нажми горячую клавишу → выдели зону → Enter")
    print("  Ctrl+C для выхода")
    print("=" * 50)

    init_tts()

    t = threading.Thread(target=hotkey_listener, daemon=True)
    t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Выход.")
        sys.exit(0)


if __name__ == "__main__":
    main()
