import tkinter as tk
import threading
import queue
import time
import sys
import os

# ─── Настройки ───────────────────────────────────────────────
HOTKEY       = "f9"
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
OCR_LANG     = "rus+eng"
TTS_RATE     = 175          # скорость речи
# ──────────────────────────────────────────────────────────────

try:
    from PIL import Image, ImageGrab, ImageTk
except ImportError:
    os.system("pip install Pillow -q")
    from PIL import Image, ImageGrab, ImageTk

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

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# ─── TTS: один поток + очередь ────────────────────────────────
tts_queue = queue.Queue()

def tts_worker():
    """Единственный поток TTS. Живёт всё время работы программы."""
    engine = pyttsx3.init()
    engine.setProperty("rate", TTS_RATE)
    voices = engine.getProperty("voices")
    for v in voices:
        if "russian" in v.name.lower() or "ru" in v.id.lower():
            engine.setProperty("voice", v.id)
            break

    while True:
        text = tts_queue.get()  # ждём задание
        if text is None:
            break
        try:
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"[TTS error] {e}")
        tts_queue.task_done()

def speak(text):
    text = text.strip()
    if not text:
        print("[!] Текст не распознан.")
        return
    print(f"[TTS] Озвучиваю: {text[:100]}{'...' if len(text)>100 else ''}")
    tts_queue.put(text)


# ─── Скриншот (обход DirectX/OpenGL) ─────────────────────────
def take_screenshot() -> Image.Image:
    """
    ImageGrab.grab(all_screens=True) захватывает через GDI —
    работает с большинством игр где mss даёт чёрный экран.
    Если и это не поможет — попробуем dxcam как fallback.
    """
    try:
        img = ImageGrab.grab(all_screens=True)
        # Проверяем: если изображение полностью чёрное — пробуем dxcam
        extrema = img.convert("L").getextrema()
        if extrema == (0, 0):
            raise RuntimeError("black screen")
        return img
    except Exception as e:
        print(f"[!] ImageGrab не сработал ({e}), пробую dxcam...")
        return screenshot_dxcam()

def screenshot_dxcam() -> Image.Image:
    try:
        import dxcam
    except ImportError:
        print("[!] Устанавливаю dxcam...")
        os.system("pip install dxcam -q")
        import dxcam
    camera = dxcam.create()
    frame = camera.grab()
    camera.release()
    if frame is None:
        raise RuntimeError("dxcam вернул None")
    return Image.fromarray(frame)


# ─── Окно выделения зоны ──────────────────────────────────────
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

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.scale_x = sw / screenshot_img.width
        self.scale_y = sh / screenshot_img.height

        self.canvas = tk.Canvas(self.root, width=sw, height=sh,
                                highlightthickness=0, cursor="crosshair",
                                bg="black")
        self.canvas.pack(fill="both", expand=True)

        resized = screenshot_img.resize((sw, sh), Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(resized)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)

        self.canvas.create_rectangle(0, 0, sw, 30, fill="#000000")
        self.canvas.create_text(sw//2, 15, fill="#ffffff", font=("Arial", 12),
            text="Выделите зону мышью → Enter  |  Esc — отмена")

        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Return>", self._on_enter)
        self.root.bind("<Escape>", self._on_escape)

    def _on_press(self, e):
        self.start_x, self.start_y = e.x, e.y
        self.end_x,   self.end_y   = e.x, e.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)

    def _on_drag(self, e):
        self.end_x, self.end_y = e.x, e.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y, e.x, e.y,
            outline="#ff0000", width=2, dash=(4, 2))

    def _on_release(self, e):
        self.end_x, self.end_y = e.x, e.y

    def _on_enter(self, e):
        x1 = min(self.start_x, self.end_x)
        y1 = min(self.start_y, self.end_y)
        x2 = max(self.start_x, self.end_x)
        y2 = max(self.start_y, self.end_y)
        if x2 - x1 < 5 or y2 - y1 < 5:
            return
        self.result = (
            int(x1 / self.scale_x), int(y1 / self.scale_y),
            int(x2 / self.scale_x), int(y2 / self.scale_y)
        )
        self.root.destroy()

    def _on_escape(self, e):
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result


# ─── OCR ──────────────────────────────────────────────────────
def ocr_region(img: Image.Image, region: tuple) -> str:
    x1, y1, x2, y2 = region
    cropped = img.crop((x1, y1, x2, y2))
    w, h = cropped.size
    # Увеличиваем для лучшего распознавания мелкого текста
    scale = max(2, int(400 / min(w, h))) if min(w, h) < 200 else 2
    cropped = cropped.resize((w * scale, h * scale), Image.LANCZOS)
    return pytesseract.image_to_string(cropped, lang=OCR_LANG, config="--psm 6")


# ─── Основной сценарий ────────────────────────────────────────
def trigger():
    print(f"\n[{HOTKEY.upper()}] Делаю скриншот...")
    time.sleep(0.2)

    try:
        img = take_screenshot()
    except Exception as e:
        print(f"[!] Ошибка скриншота: {e}")
        return

    print("[OK] Скриншот готов. Открываю окно выделения...")
    overlay = SelectionOverlay(img)
    region  = overlay.run()

    if region is None:
        print("[!] Выделение отменено.")
        return

    print(f"[OCR] Распознаю зону {region}...")
    text = ocr_region(img, region)

    if not text.strip():
        print("[!] Текст не найден.")
    else:
        speak(text)


def hotkey_listener():
    keyboard.add_hotkey(HOTKEY,
        lambda: threading.Thread(target=trigger, daemon=True).start())
    while True:
        try:
            keyboard.read_event(suppress=False)
        except Exception:
            time.sleep(0.05)


# ─── Запуск ───────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  Screen Reader — Озвучка текста с экрана")
    print("=" * 50)
    print(f"  Горячая клавиша : {HOTKEY.upper()}")
    print(f"  Языки OCR       : {OCR_LANG}")
    print("=" * 50)
    print("  Нажми F9 → выдели зону → Enter")
    print("  Ctrl+C для выхода")
    print("=" * 50)

    # Запускаем TTS-поток (живёт всё время)
    t_tts = threading.Thread(target=tts_worker, daemon=True)
    t_tts.start()

    # Запускаем слушатель горячей клавиши
    t_key = threading.Thread(target=hotkey_listener, daemon=True)
    t_key.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        tts_queue.put(None)   # останавливаем TTS-поток
        print("\n[!] Выход.")
        sys.exit(0)


if __name__ == "__main__":
    main()
