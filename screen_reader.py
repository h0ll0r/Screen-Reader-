import tkinter as tk
from tkinter import messagebox
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
    import mss.tools

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
HOTKEY = "f9"  # Горячая клавиша (можно поменять)
# Путь к tesseract — поменяй если установлен в другое место
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
OCR_LANG = "rus+eng"
# ──────────────────────────────────────────────────────────────

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# TTS движок
tts_engine = None
tts_lock = threading.Lock()

def init_tts():
    global tts_engine
    tts_engine = pyttsx3.init()
    tts_engine.setProperty("rate", 175)  # скорость речи
    # Выбираем русский голос если есть
    voices = tts_engine.getProperty("voices")
    for v in voices:
        if "russian" in v.name.lower() or "ru" in v.id.lower():
            tts_engine.setProperty("voice", v.id)
            break

def speak(text):
    """Озвучить текст в отдельном потоке."""
    text = text.strip()
    if not text:
        print("[!] Текст не распознан или зона пустая.")
        return
    print(f"[TTS] Озвучиваю: {text[:80]}{'...' if len(text)>80 else ''}")
    def _run():
        with tts_lock:
            tts_engine.say(text)
            tts_engine.runAndWait()
    threading.Thread(target=_run, daemon=True).start()


class SelectionOverlay:
    """Полноэкранное окно для выделения зоны."""

    def __init__(self, screenshot_img: Image.Image):
        self.screenshot = screenshot_img
        self.result = None  # (x1,y1,x2,y2) в пикселях оригинала

        self.root = tk.Tk()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.configure(cursor="crosshair")
        self.root.title("Выделите зону — Enter для распознавания, Esc для отмены")

        # Канвас на весь экран
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        self.canvas = tk.Canvas(self.root, width=sw, height=sh,
                                highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        # Масштабируем скриншот под экран
        self.scale_x = sw / screenshot_img.width
        self.scale_y = sh / screenshot_img.height
        resized = screenshot_img.resize((sw, sh), Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(resized)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)

        # Полупрозрачный оверлей-подсказка
        self.canvas.create_rectangle(0, 0, sw, 30, fill="#000000")
        self.canvas.create_text(sw//2, 15, fill="#ffffff", font=("Arial", 12),
            text="Выделите зону мышью → нажмите Enter  |  Esc — отмена")

        # Переменные выделения
        self.start_x = self.start_y = 0
        self.rect_id = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Return>", self._on_enter)
        self.root.bind("<Escape>", self._on_escape)

    def _on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)

    def _on_drag(self, event):
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
        # Переводим обратно в оригинальные координаты
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
    with mss.mss() as sct:
        monitor = sct.monitors[0]  # весь экран
        sct_img = sct.grab(monitor)
        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")


def ocr_region(img: Image.Image, region: tuple) -> str:
    x1, y1, x2, y2 = region
    cropped = img.crop((x1, y1, x2, y2))
    # Небольшое увеличение для лучшего OCR
    w, h = cropped.size
    cropped = cropped.resize((w * 2, h * 2), Image.LANCZOS)
    text = pytesseract.image_to_string(cropped, lang=OCR_LANG,
                                       config="--psm 6")
    return text


def trigger():
    """Вызывается по горячей клавише."""
    print(f"\n[{HOTKEY.upper()}] Делаю скриншот...")
    # Небольшая задержка чтобы клавиша успела отпуститься
    time.sleep(0.15)
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


def main():
    print("=" * 50)
    print("  Screen Reader — Озвучка текста с экрана")
    print("=" * 50)
    print(f"  Горячая клавиша : {HOTKEY.upper()}")
    print(f"  Tesseract путь  : {TESSERACT_PATH}")
    print(f"  Языки OCR       : {OCR_LANG}")
    print("=" * 50)
    print("  Нажми горячую клавишу → выдели зону → Enter")
    print("  Ctrl+C для выхода")
    print("=" * 50)

    init_tts()

    keyboard.add_hotkey(HOTKEY, trigger, suppress=True)

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\n[!] Выход.")
        sys.exit(0)


if __name__ == "__main__":
    main()
